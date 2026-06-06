"""Coordinator for SolarTouch LAN — manages connection modes and polling."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval, async_track_point_in_time
import homeassistant.util.dt as dt_util

from .const import (
    CONF_CONNECTION_MODE,
    CONF_PERIODIC_INTERVAL,
    CONF_DAILY_CLOUD_SYNC,
    CONNECTION_MODE_LIVE,
    CONNECTION_MODE_STANDBY,
    CONNECTION_MODE_PERIODIC,
    PERIODIC_INTERVAL_DEFAULT,
    LIVE_SESSION_SECONDS,
    WRITE_LINGER_SECONDS,
    CLOUD_SYNC_PAUSE_SECONDS,
    CLOUD_SYNC_HOUR,
    CLOUD_SYNC_MINUTE,
    SCAN_FAST_SECONDS,
    SCAN_LIVE_FAST_SECONDS,
    SENSOR_DEFINITIONS,
)
from .modbus_client import SolarMaxModbusClient, ModbusConnectionError

_LOGGER = logging.getLogger(__name__)

STATUS_STANDBY = "standby"
STATUS_LIVE = "live"
STATUS_PERIODIC = "periodic"
STATUS_CONNECTING = "connecting"
STATUS_ERROR = "error"

COUNTDOWN_TICK_SECONDS = 1
SLOW_POLL_RATIO = 6  # include slow sensors every 6th live-mode tick (6×10s = 60s)


class SolarTouchLANCoordinator:
    """Manages polling, connection lifecycle, and session timers.

    In every mode the inverter connection is opened, data is pulled, then
    immediately closed so the inverter can resume pushing data to the cloud.
    """

    def __init__(self, hass: HomeAssistant, entry_data: dict) -> None:
        self.hass = hass
        self._mode = entry_data[CONF_CONNECTION_MODE]
        self._daily_sync = entry_data.get(CONF_DAILY_CLOUD_SYNC, False)
        self._periodic_interval_minutes: int = entry_data.get(
            CONF_PERIODIC_INTERVAL, PERIODIC_INTERVAL_DEFAULT
        )
        self.client = SolarMaxModbusClient(
            host=entry_data["host"],
            port=entry_data["port"],
            slave=entry_data["slave"],
        )
        self.data: dict[str, Any] = {}
        self.status: str = STATUS_STANDBY
        self.last_poll: datetime | None = None

        self.live_session_remaining: int = 0
        self.next_poll_in: int = 0

        self._live_session_end_time: datetime | None = None
        self._next_poll_at: datetime | None = None
        self._poll_counter: int = 0

        self._fast_defs = [d for d in SENSOR_DEFINITIONS if d["fast"]]
        self._slow_defs = [d for d in SENSOR_DEFINITIONS if not d["fast"]]

        self._poll_unsub = None
        self._countdown_unsub = None
        self._live_timer_handle = None
        self._cloud_sync_unsub = None
        self._cloud_sync_active = False
        self._cloud_sync_resume_handle = None

        # Prevents overlapping connect/poll/disconnect cycles
        self._poll_lock = asyncio.Lock()

        self._listeners: list[Any] = []

    # ── Public API ────────────────────────────────────────────────────────────

    async def async_setup(self) -> None:
        await self._async_initial_fetch()
        if self._mode == CONNECTION_MODE_LIVE:
            self._start_live_polling()
        elif self._mode == CONNECTION_MODE_PERIODIC:
            self._start_periodic_polling()
        if self._daily_sync:
            self._schedule_next_cloud_sync()

    async def _async_initial_fetch(self) -> None:
        """Connect → poll all sensors → disconnect to populate state on startup."""
        self.status = STATUS_CONNECTING
        self._notify_listeners()
        try:
            await self.client.async_connect()
            await self._async_read_all()
            self.last_poll = dt_util.utcnow()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Initial fetch failed: %s", err)
        finally:
            await self.client.async_disconnect()
            self.status = self._idle_status()
            self._notify_listeners()

    async def async_shutdown(self) -> None:
        self._stop_polling()
        if self._live_timer_handle:
            self._live_timer_handle()
            self._live_timer_handle = None
        if self._cloud_sync_unsub:
            self._cloud_sync_unsub()
        if self._cloud_sync_resume_handle:
            self._cloud_sync_resume_handle()
        await self.client.async_disconnect()

    async def async_go_live(self) -> None:
        """Start or extend a 5-minute live session (standby mode only)."""
        if self._cloud_sync_active or self._mode != CONNECTION_MODE_STANDBY:
            return
        if not self._poll_unsub:
            self._start_session_polling()
        self._reset_live_timer(LIVE_SESSION_SECONDS)
        self.status = STATUS_LIVE

    async def async_pause_sync(self) -> None:
        """Stop any active polling and return to standby."""
        if self._live_timer_handle:
            self._live_timer_handle()
            self._live_timer_handle = None
        self._live_session_end_time = None
        self.live_session_remaining = 0
        self._stop_polling()
        await self.client.async_disconnect()
        self.status = STATUS_STANDBY
        self._notify_listeners()

    async def async_set_mode(self, mode: str) -> None:
        """Switch between standby, periodic, and live at runtime."""
        if self._mode == mode:
            return
        self._stop_polling()
        if self._live_timer_handle:
            self._live_timer_handle()
            self._live_timer_handle = None
        self._live_session_end_time = None
        self.live_session_remaining = 0
        await self.client.async_disconnect()
        self._mode = mode
        if mode == CONNECTION_MODE_LIVE:
            self._poll_counter = 0
            self._start_live_polling()
        elif mode == CONNECTION_MODE_PERIODIC:
            self._start_periodic_polling()
        self.status = self._idle_status()
        self._notify_listeners()

    async def async_set_periodic_interval(self, minutes: int) -> None:
        """Change the periodic poll interval; restarts polling if currently active."""
        self._periodic_interval_minutes = minutes
        if self._mode == CONNECTION_MODE_PERIODIC and self._poll_unsub:
            self._stop_polling()
            self._start_periodic_polling()

    async def async_force_refresh(self) -> None:
        """Immediately connect, poll all sensors, and disconnect."""
        await self._async_poll_all()

    async def async_write_register(self, address: int, value: int, uint32: bool = False) -> None:
        """Connect, write a register, then disconnect."""
        async with self._poll_lock:
            try:
                await self.client.async_connect()
                if uint32:
                    await self.client.async_write_register_uint32(address, value)
                else:
                    await self.client.async_write_register(address, value)
            except ModbusConnectionError as err:
                _LOGGER.error("Write failed: %s", err)
                self.status = STATUS_ERROR
                self._notify_listeners()
                return
            finally:
                await self.client.async_disconnect()

        if self._mode == CONNECTION_MODE_STANDBY:
            if not self._poll_unsub:
                self._start_session_polling()
            self._reset_live_timer(WRITE_LINGER_SECONDS)
            self.status = STATUS_LIVE
            self._notify_listeners()

    def register_listener(self, callback_fn) -> None:
        self._listeners.append(callback_fn)

    def unregister_listener(self, callback_fn) -> None:
        if callback_fn in self._listeners:
            self._listeners.remove(callback_fn)

    # ── Polling ───────────────────────────────────────────────────────────────

    def _start_live_polling(self) -> None:
        """30 s interval; connect → fast (+ slow every 10th tick) → disconnect."""
        if self._poll_unsub:
            return
        self._next_poll_at = dt_util.utcnow() + timedelta(seconds=SCAN_FAST_SECONDS)
        self._poll_unsub = async_track_time_interval(
            self.hass, self._async_live_poll_tick, timedelta(seconds=SCAN_FAST_SECONDS)
        )
        self._start_countdown_ticker()

    def _start_session_polling(self) -> None:
        """Fast polling for a standby Go Live session; also fires an immediate full poll."""
        if self._poll_unsub:
            return
        self._next_poll_at = dt_util.utcnow() + timedelta(seconds=SCAN_LIVE_FAST_SECONDS)
        self._poll_unsub = async_track_time_interval(
            self.hass, self._async_session_poll_tick, timedelta(seconds=SCAN_LIVE_FAST_SECONDS)
        )
        self._start_countdown_ticker()
        self.hass.async_create_task(self._async_poll_all())

    def _start_periodic_polling(self) -> None:
        """N-minute interval; connect → poll all → disconnect."""
        if self._poll_unsub:
            return
        interval_secs = self._periodic_interval_minutes * 60
        self._next_poll_at = dt_util.utcnow() + timedelta(seconds=interval_secs)
        self._poll_unsub = async_track_time_interval(
            self.hass, self._async_periodic_poll_tick, timedelta(seconds=interval_secs)
        )
        self._start_countdown_ticker()

    def _start_countdown_ticker(self) -> None:
        if self._countdown_unsub:
            return
        self._countdown_unsub = async_track_time_interval(
            self.hass, self._async_tick_countdowns, timedelta(seconds=COUNTDOWN_TICK_SECONDS)
        )

    def _stop_polling(self) -> None:
        if self._poll_unsub:
            self._poll_unsub()
            self._poll_unsub = None
        if self._countdown_unsub:
            self._countdown_unsub()
            self._countdown_unsub = None
        self._next_poll_at = None
        self.next_poll_in = 0

    @callback
    def _async_tick_countdowns(self, _now=None) -> None:
        now = dt_util.utcnow()
        if self._live_session_end_time:
            self.live_session_remaining = max(
                0, int((self._live_session_end_time - now).total_seconds())
            )
        else:
            self.live_session_remaining = 0
        if self._next_poll_at:
            self.next_poll_in = max(0, int((self._next_poll_at - now).total_seconds()))
        else:
            self.next_poll_in = 0
        self._notify_listeners()

    async def _async_live_poll_tick(self, _now=None) -> None:
        """Live mode tick: connect → fast sensors (+ slow every 10th) → disconnect."""
        if self._cloud_sync_active:
            return
        async with self._poll_lock:
            self._poll_counter += 1
            include_slow = (self._poll_counter % SLOW_POLL_RATIO == 0)
            self._next_poll_at = dt_util.utcnow() + timedelta(seconds=SCAN_FAST_SECONDS)
            try:
                await self.client.async_connect()
                new_data = await self.client.async_read_registers_batch(self._fast_defs)
                if include_slow:
                    slow_data = await self.client.async_read_registers_batch(self._slow_defs)
                    new_data.update(slow_data)
                self.data.update(new_data)
                self.last_poll = dt_util.utcnow()
                if self.status == STATUS_ERROR:
                    self.status = STATUS_LIVE
            except ModbusConnectionError as err:
                _LOGGER.warning("Live poll error: %s", err)
                self.status = STATUS_ERROR
            finally:
                await self.client.async_disconnect()
        self._notify_listeners()

    async def _async_session_poll_tick(self, _now=None) -> None:
        """Go Live session tick: connect → fast sensors → disconnect."""
        if self._cloud_sync_active:
            return
        async with self._poll_lock:
            self._next_poll_at = dt_util.utcnow() + timedelta(seconds=SCAN_LIVE_FAST_SECONDS)
            try:
                await self.client.async_connect()
                new_data = await self.client.async_read_registers_batch(self._fast_defs)
                self.data.update(new_data)
                self.last_poll = dt_util.utcnow()
            except ModbusConnectionError as err:
                _LOGGER.warning("Session poll error: %s", err)
                self.status = STATUS_ERROR
            finally:
                await self.client.async_disconnect()
        self._notify_listeners()

    async def _async_periodic_poll_tick(self, _now=None) -> None:
        """Periodic mode tick: update next-poll countdown then poll all."""
        if self._cloud_sync_active:
            return
        self._next_poll_at = dt_util.utcnow() + timedelta(
            minutes=self._periodic_interval_minutes
        )
        await self._async_poll_all()

    async def _async_poll_all(self) -> None:
        """Connect → poll every sensor → disconnect."""
        async with self._poll_lock:
            try:
                await self.client.async_connect()
                await self._async_read_all()
                self.last_poll = dt_util.utcnow()
                if self.status == STATUS_ERROR:
                    self.status = self._idle_status()
            except ModbusConnectionError as err:
                _LOGGER.warning("Full poll error: %s", err)
                self.status = STATUS_ERROR
            finally:
                await self.client.async_disconnect()
        self._notify_listeners()

    async def _async_read_all(self) -> None:
        """Read fast and slow registers (connection must already be open)."""
        fast = await self.client.async_read_registers_batch(self._fast_defs)
        slow = await self.client.async_read_registers_batch(self._slow_defs)
        self.data.update(fast)
        self.data.update(slow)

    def _idle_status(self) -> str:
        if self._mode == CONNECTION_MODE_LIVE:
            return STATUS_LIVE
        if self._mode == CONNECTION_MODE_PERIODIC:
            return STATUS_PERIODIC
        return STATUS_STANDBY

    def _reset_live_timer(self, seconds: int) -> None:
        if self._live_timer_handle:
            self._live_timer_handle()
        fire_at = dt_util.utcnow() + timedelta(seconds=seconds)
        self._live_session_end_time = fire_at
        self.live_session_remaining = seconds
        self._live_timer_handle = async_track_point_in_time(
            self.hass, self._async_live_session_expired, fire_at
        )
        self._notify_listeners()

    async def _async_live_session_expired(self, _now=None) -> None:
        self._live_timer_handle = None
        self._live_session_end_time = None
        self.live_session_remaining = 0
        self.next_poll_in = 0
        self._stop_polling()
        await self.client.async_disconnect()
        self.status = STATUS_STANDBY
        self._notify_listeners()

    # ── Daily cloud sync ──────────────────────────────────────────────────────

    def _schedule_next_cloud_sync(self) -> None:
        now = dt_util.now()
        target = now.replace(
            hour=CLOUD_SYNC_HOUR, minute=CLOUD_SYNC_MINUTE, second=0, microsecond=0
        )
        if target <= now:
            target = target + timedelta(days=1)
        self._cloud_sync_unsub = async_track_point_in_time(
            self.hass, self._async_cloud_sync_start, target
        )

    async def _async_cloud_sync_start(self, _now=None) -> None:
        _LOGGER.info("Daily cloud sync: pausing for %s seconds", CLOUD_SYNC_PAUSE_SECONDS)
        self._cloud_sync_active = True
        self._cloud_sync_unsub = None
        self._stop_polling()
        await self.client.async_disconnect()
        self.status = STATUS_STANDBY
        self._notify_listeners()
        resume_at = dt_util.utcnow() + timedelta(seconds=CLOUD_SYNC_PAUSE_SECONDS)
        self._cloud_sync_resume_handle = async_track_point_in_time(
            self.hass, self._async_cloud_sync_end, resume_at
        )

    async def _async_cloud_sync_end(self, _now=None) -> None:
        _LOGGER.info("Daily cloud sync: resuming")
        self._cloud_sync_active = False
        self._cloud_sync_resume_handle = None
        self._schedule_next_cloud_sync()
        if self._mode == CONNECTION_MODE_LIVE:
            self._start_live_polling()
        elif self._mode == CONNECTION_MODE_PERIODIC:
            self._start_periodic_polling()
        self.status = self._idle_status()
        self._notify_listeners()

    @callback
    def _notify_listeners(self) -> None:
        for cb in self._listeners:
            cb()
