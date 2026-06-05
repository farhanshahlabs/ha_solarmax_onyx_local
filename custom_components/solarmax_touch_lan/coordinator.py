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
    CONF_DAILY_CLOUD_SYNC,
    CONNECTION_MODE_ALWAYS_ON,
    CONNECTION_MODE_STANDBY,
    LIVE_SESSION_SECONDS,
    WRITE_LINGER_SECONDS,
    CLOUD_SYNC_PAUSE_SECONDS,
    CLOUD_SYNC_HOUR,
    CLOUD_SYNC_MINUTE,
    SCAN_FAST_SECONDS,
    SCAN_SLOW_SECONDS,
    SENSOR_DEFINITIONS,
)
from .modbus_client import SolarMaxModbusClient, ModbusConnectionError

_LOGGER = logging.getLogger(__name__)

STATUS_STANDBY = "standby"
STATUS_LIVE = "live"
STATUS_CONNECTING = "connecting"
STATUS_ERROR = "error"


class SolarTouchLANCoordinator:
    """Manages polling, connection lifecycle, and session timers."""

    def __init__(self, hass: HomeAssistant, entry_data: dict) -> None:
        self.hass = hass
        self._mode = entry_data[CONF_CONNECTION_MODE]
        self._daily_sync = entry_data.get(CONF_DAILY_CLOUD_SYNC, False)
        self.client = SolarMaxModbusClient(
            host=entry_data["host"],
            port=entry_data["port"],
            slave=entry_data["slave"],
        )
        self.data: dict[str, Any] = {}
        self.status: str = STATUS_STANDBY
        self.last_poll: datetime | None = None
        self.live_session_remaining: int = 0

        self._fast_defs = [d for d in SENSOR_DEFINITIONS if d["fast"]]
        self._slow_defs = [d for d in SENSOR_DEFINITIONS if not d["fast"]]

        self._fast_unsub = None
        self._slow_unsub = None
        self._live_timer_handle = None
        self._cloud_sync_unsub = None
        self._cloud_sync_active = False
        self._cloud_sync_resume_handle = None
        self._slow_tick = 0

        self._listeners: list[Any] = []

    # ── Public API ────────────────────────────────────────────────────────────

    async def async_setup(self) -> None:
        if self._mode == CONNECTION_MODE_ALWAYS_ON:
            await self._async_connect()
            self._start_polling()
        else:
            # Schedule initial fetch as a background task so it runs AFTER
            # all entities are registered and listeners are attached.
            self.hass.async_create_task(self._async_initial_fetch())

        if self._daily_sync:
            self._schedule_next_cloud_sync()

    async def _async_initial_fetch(self) -> None:
        """One-shot connect → poll → disconnect to populate values on startup."""
        self.status = STATUS_CONNECTING
        self._notify_listeners()
        try:
            await self.client.async_connect()
            await self._async_poll_all()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Initial fetch failed: %s", err)
        finally:
            await self.client.async_disconnect()
            self.status = STATUS_STANDBY
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
        """Activate a live session (standby mode only)."""
        if self._mode != CONNECTION_MODE_STANDBY:
            return
        if self._cloud_sync_active:
            return
        await self._async_connect()
        self._start_polling()
        self._reset_live_timer(LIVE_SESSION_SECONDS)

    async def async_force_refresh(self) -> None:
        """Immediately poll all sensors regardless of mode/timer."""
        if not self.client.connected:
            if self._mode == CONNECTION_MODE_STANDBY:
                await self.async_go_live()
                return
        await self._async_poll_all()

    async def async_write_register(self, address: int, value: int, uint32: bool = False) -> None:
        """Write a register and extend the live session."""
        if not self.client.connected:
            await self._async_connect()
            self._start_polling()
        try:
            if uint32:
                await self.client.async_write_register_uint32(address, value)
            else:
                await self.client.async_write_register(address, value)
        except ModbusConnectionError as err:
            _LOGGER.error("Write failed: %s", err)
            self.status = STATUS_ERROR
            self._notify_listeners()
            return

        if self._mode == CONNECTION_MODE_STANDBY:
            self._reset_live_timer(WRITE_LINGER_SECONDS)

    def register_listener(self, callback_fn) -> None:
        self._listeners.append(callback_fn)

    def unregister_listener(self, callback_fn) -> None:
        self._listeners.discard(callback_fn) if hasattr(self._listeners, "discard") else None
        if callback_fn in self._listeners:
            self._listeners.remove(callback_fn)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _async_connect(self) -> None:
        self.status = STATUS_CONNECTING
        self._notify_listeners()
        try:
            await self.client.async_connect()
            self.status = STATUS_LIVE
        except ModbusConnectionError as err:
            _LOGGER.error("Connection failed: %s", err)
            self.status = STATUS_ERROR
        self._notify_listeners()

    def _start_polling(self) -> None:
        if self._fast_unsub:
            return
        self._fast_unsub = async_track_time_interval(
            self.hass, self._async_fast_poll, timedelta(seconds=SCAN_FAST_SECONDS)
        )
        self._slow_unsub = async_track_time_interval(
            self.hass, self._async_slow_poll, timedelta(seconds=SCAN_SLOW_SECONDS)
        )
        # Kick off an immediate poll
        self.hass.async_create_task(self._async_poll_all())

    def _stop_polling(self) -> None:
        if self._fast_unsub:
            self._fast_unsub()
            self._fast_unsub = None
        if self._slow_unsub:
            self._slow_unsub()
            self._slow_unsub = None

    async def _async_fast_poll(self, _now=None) -> None:
        if not self.client.connected:
            return
        try:
            new_data = await self.client.async_read_registers_batch(self._fast_defs)
            self.data.update(new_data)
            self.last_poll = dt_util.utcnow()
        except ModbusConnectionError as err:
            _LOGGER.warning("Fast poll error: %s", err)
            self.status = STATUS_ERROR
        self._notify_listeners()

    async def _async_slow_poll(self, _now=None) -> None:
        if not self.client.connected:
            return
        try:
            new_data = await self.client.async_read_registers_batch(self._slow_defs)
            self.data.update(new_data)
            self.last_poll = dt_util.utcnow()
        except ModbusConnectionError as err:
            _LOGGER.warning("Slow poll error: %s", err)
            self.status = STATUS_ERROR
        self._notify_listeners()

    async def _async_poll_all(self) -> None:
        if not self.client.connected:
            return
        try:
            fast = await self.client.async_read_registers_batch(self._fast_defs)
            slow = await self.client.async_read_registers_batch(self._slow_defs)
            self.data.update(fast)
            self.data.update(slow)
            self.last_poll = dt_util.utcnow()
            if self.status == STATUS_ERROR:
                self.status = STATUS_LIVE
        except ModbusConnectionError as err:
            _LOGGER.warning("Full poll error: %s", err)
            self.status = STATUS_ERROR
        self._notify_listeners()

    def _reset_live_timer(self, seconds: int) -> None:
        if self._live_timer_handle:
            self._live_timer_handle()
        self.live_session_remaining = seconds
        fire_at = dt_util.utcnow() + timedelta(seconds=seconds)
        self._live_timer_handle = async_track_point_in_time(
            self.hass, self._async_live_session_expired, fire_at
        )
        self._notify_listeners()

    async def _async_live_session_expired(self, _now=None) -> None:
        self._live_timer_handle = None
        self.live_session_remaining = 0
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
        _LOGGER.info("Daily cloud sync: pausing local connection for %s seconds", CLOUD_SYNC_PAUSE_SECONDS)
        self._cloud_sync_active = True
        self._cloud_sync_unsub = None

        if self.client.connected:
            self._stop_polling()
            await self.client.async_disconnect()
            self.status = STATUS_STANDBY
            self._notify_listeners()

        resume_at = dt_util.utcnow() + timedelta(seconds=CLOUD_SYNC_PAUSE_SECONDS)
        self._cloud_sync_resume_handle = async_track_point_in_time(
            self.hass, self._async_cloud_sync_end, resume_at
        )

    async def _async_cloud_sync_end(self, _now=None) -> None:
        _LOGGER.info("Daily cloud sync: resuming local connection")
        self._cloud_sync_active = False
        self._cloud_sync_resume_handle = None
        self._schedule_next_cloud_sync()

        if self._mode == CONNECTION_MODE_ALWAYS_ON:
            await self._async_connect()
            self._start_polling()
        self._notify_listeners()

    @callback
    def _notify_listeners(self) -> None:
        for cb in self._listeners:
            cb()
