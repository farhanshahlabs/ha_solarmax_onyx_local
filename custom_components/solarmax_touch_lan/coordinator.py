"""Coordinator for SolarTouch LAN — manages connection modes and polling."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_time_change,
    async_track_time_interval,
)
import homeassistant.util.dt as dt_util

from .const import (
    DOMAIN,
    CONF_CONNECTION_MODE,
    CONF_PERIODIC_INTERVAL,
    CONF_DAILY_CLOUD_SYNC,
    CONF_RATED_POWER,
    CONF_RATED_POWER_MANUAL,
    CONF_MODEL,
    CONF_SERIAL,
    CONF_SW_VERSION,
    DEVICE_INFO_REGISTER,
    DEVICE_INFO_COUNT,
    DEVICE_INFO_MODEL,
    DEVICE_INFO_SERIAL,
    DEVICE_INFO_MASTER_FW,
    DEVICE_INFO_SLAVE_FW,
    DEVICE_INFO_EMS_FW_REGISTER,
    DEVICE_INFO_RATED_POWER,
    RATED_POWER_DEFAULT_KW,
    CLOCK_REGISTER,
    CLOCK_SYNC_HOUR,
    CLOCK_SYNC_TOLERANCE_SECONDS,
    CONF_AUTO_CLOCK_SYNC,
    RATED_POWER_MIN_KW,
    RATED_POWER_MAX_KW,
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
from .modbus_client import SolarMaxModbusClient, ModbusConnectionError, decode_ascii

_LOGGER = logging.getLogger(__name__)

STATUS_STANDBY = "standby"
STATUS_LIVE = "live"
STATUS_PERIODIC = "periodic"
STATUS_CONNECTING = "connecting"
STATUS_ERROR = "error"

COUNTDOWN_TICK_SECONDS = 1
SLOW_POLL_RATIO = 6  # include slow sensors every 6th live-mode tick (6×10s = 60s)

# Connection retries for a single poll / write cycle. The inverter shares its
# one Modbus slot with the cloud uploader, so refusals are usually transient.
POLL_CONNECT_ATTEMPTS = 2
WRITE_CONNECT_ATTEMPTS = 3

# Register-write retry/verify: the inverter intermittently drops writes, so
# every write is read back and retried until the register actually holds the
# requested value.
WRITE_MAX_ATTEMPTS = 2                 # write+verify attempts within one connection
WRITE_VERIFY_SETTLE_SECONDS = 0.5      # let the inverter apply the write before reading back
WRITE_RETRY_DELAY_SECONDS = 1.0        # wait between attempts within one connection

# Queued writes are retried in the background, reconnecting each cycle, until
# confirmed or the deadline passes. Long enough to outlast the nightly cloud
# sync pause and a few minutes of the cloud holding the connection.
WRITE_DEADLINE_SECONDS = 900
WRITE_CYCLE_BACKOFF_SECONDS = (5, 10, 20, 30, 60)

EVENT_WRITE_CONFIRMED = f"{DOMAIN}_write_confirmed"
EVENT_WRITE_FAILED = f"{DOMAIN}_write_failed"

# Lifetime counters (TOTAL_INCREASING) can never go down and cannot jump by
# more than this between two polls. A reading that breaks either rule is a
# glitch and is dropped, unless it repeats this many times in a row (a real
# counter reset on the inverter), in which case it is accepted.
COUNTER_MAX_STEP = 500
COUNTER_CONFIRM_READINGS = 3


@dataclass
class PendingWrite:
    """A register write waiting to be confirmed by the inverter."""

    name: str
    address: int
    value: int
    uint32: bool
    data_key: str | None
    data_value: Any
    deadline: datetime
    attempts: int = 0


class SolarTouchLANCoordinator:
    """Manages polling, connection lifecycle, and session timers.

    In every mode the inverter connection is opened, data is pulled, then
    immediately closed so the inverter can resume pushing data to the cloud.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        entry_data = entry.data
        self._mode = entry_data[CONF_CONNECTION_MODE]
        self._daily_sync = entry_data.get(CONF_DAILY_CLOUD_SYNC, False)
        # Options (set in the integration's Configure dialog) override data.
        options = entry.options
        self._daily_sync = options.get(CONF_DAILY_CLOUD_SYNC, self._daily_sync)
        self._auto_clock_sync: bool = options.get(CONF_AUTO_CLOCK_SYNC, False)
        self._clock_sync_unsub = None
        self._clock_read_at: datetime | None = None
        self._periodic_interval_minutes: int = entry_data.get(
            CONF_PERIODIC_INTERVAL, PERIODIC_INTERVAL_DEFAULT
        )
        self.rated_power_kw: float = entry_data.get(
            CONF_RATED_POWER, RATED_POWER_DEFAULT_KW
        )
        self._rated_power_manual: bool = entry_data.get(CONF_RATED_POWER_MANUAL, False)
        self._device_info: dict[str, str] = {
            k: entry_data[k] for k in (CONF_MODEL, CONF_SERIAL, CONF_SW_VERSION)
            if entry_data.get(k)
        }
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
        self._counter_keys = {
            d["key"] for d in SENSOR_DEFINITIONS
            if d["state_class"] == SensorStateClass.TOTAL_INCREASING
        }
        # key -> (rejected value, consecutive count) for suspicious counter readings
        self._counter_suspects: dict[str, tuple[float, int]] = {}

        # Writes waiting to be confirmed, keyed by register address. A newer
        # request for the same register replaces the older one.
        self.pending_writes: dict[int, PendingWrite] = {}
        self._write_task: asyncio.Task | None = None
        self._write_wakeup = asyncio.Event()

        self._poll_unsub = None
        self._countdown_unsub = None
        self._live_timer_handle = None
        self._cloud_sync_unsub = None
        self._cloud_sync_active = False
        self._cloud_sync_resume_handle = None

        # Prevents overlapping connect/poll/disconnect cycles
        self._poll_lock = asyncio.Lock()

        self._listeners: list[Any] = []
        # Entities that show a running countdown; only these are refreshed by
        # the 1-second ticker, so the other ~80 entities are not rewritten
        # every second.
        self._countdown_listeners: list[Any] = []

    # ── Persistence ───────────────────────────────────────────────────────────

    @callback
    def _persist_config(self) -> None:
        """Write the current runtime settings back to the config entry.

        Without this, mode / interval changes live only in memory and are lost
        on restart, reverting to the value chosen during initial setup.
        """
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                CONF_CONNECTION_MODE: self._mode,
                CONF_PERIODIC_INTERVAL: self._periodic_interval_minutes,
                CONF_DAILY_CLOUD_SYNC: self._daily_sync,
                CONF_RATED_POWER: self.rated_power_kw,
                CONF_RATED_POWER_MANUAL: self._rated_power_manual,
                **self._device_info,
            },
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def async_setup(self) -> None:
        await self._async_initial_fetch()
        if self._mode == CONNECTION_MODE_LIVE:
            self._start_live_polling()
        elif self._mode == CONNECTION_MODE_PERIODIC:
            self._start_periodic_polling()
        if self._daily_sync:
            self._schedule_next_cloud_sync()
        if self._auto_clock_sync:
            self._clock_sync_unsub = async_track_time_change(
                self.hass, self._async_daily_clock_sync,
                hour=CLOCK_SYNC_HOUR, minute=7, second=0,
            )

    async def _async_initial_fetch(self) -> None:
        """Connect → poll all sensors → disconnect to populate state on startup."""
        self.status = STATUS_CONNECTING
        self._notify_listeners()
        try:
            await self.client.async_connect(attempts=POLL_CONNECT_ATTEMPTS)
            await self._async_read_device_info()
            await self._async_read_all()
            self.last_poll = dt_util.utcnow()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Initial fetch failed: %s", err)
        finally:
            await self.client.async_disconnect()
            self.status = self._idle_status()
            self._notify_listeners()

    async def async_shutdown(self) -> None:
        if self._write_task and not self._write_task.done():
            self._write_task.cancel()
        self._stop_polling()
        if self._live_timer_handle:
            self._live_timer_handle()
            self._live_timer_handle = None
        if self._cloud_sync_unsub:
            self._cloud_sync_unsub()
        if self._cloud_sync_resume_handle:
            self._cloud_sync_resume_handle()
        if self._clock_sync_unsub:
            self._clock_sync_unsub()
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
        await self._async_disconnect_when_idle()
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
        await self._async_disconnect_when_idle()
        self._mode = mode
        if mode == CONNECTION_MODE_LIVE:
            self._poll_counter = 0
            self._start_live_polling()
        elif mode == CONNECTION_MODE_PERIODIC:
            self._start_periodic_polling()
        self.status = self._idle_status()
        self._persist_config()
        self._notify_listeners()

    async def async_set_periodic_interval(self, minutes: int) -> None:
        """Change the periodic poll interval; restarts polling if currently active."""
        self._periodic_interval_minutes = minutes
        if self._mode == CONNECTION_MODE_PERIODIC and self._poll_unsub:
            self._stop_polling()
            self._start_periodic_polling()
        self._persist_config()

    async def async_set_rated_power(self, kw: float) -> None:
        """Set the inverter rating by hand; it then overrides auto-detection."""
        self.rated_power_kw = kw
        self._rated_power_manual = True
        self._persist_config()
        self._notify_listeners()

    async def _async_read_device_info(self) -> None:
        """Read model, serial, firmware and rated power (connection must be open).

        Register layouts differ between firmware versions and rebrands, so
        every field is validated and anything implausible is ignored; the
        integration then keeps its previous values / defaults.
        """
        try:
            regs = await self.client.async_read_block(DEVICE_INFO_REGISTER, DEVICE_INFO_COUNT)
        except ModbusConnectionError as err:
            _LOGGER.debug("Device info not available: %s", err)
            return

        def text(field: tuple[int, int]) -> str:
            start, count = field
            return decode_ascii(regs[start:start + count])

        # Firmware shown as "master / slave / CSB", like the portal lists them.
        firmware = [text(DEVICE_INFO_MASTER_FW), text(DEVICE_INFO_SLAVE_FW)]
        try:
            firmware.append(decode_ascii(
                await self.client.async_read_block(DEVICE_INFO_EMS_FW_REGISTER, 3)
            ))
        except ModbusConnectionError as err:
            _LOGGER.debug("CSB firmware version not available: %s", err)
        info = {
            CONF_MODEL: text(DEVICE_INFO_MODEL),
            CONF_SERIAL: text(DEVICE_INFO_SERIAL),
            CONF_SW_VERSION: " / ".join(v for v in firmware if len(v) >= 3),
        }
        info = {k: v for k, v in info.items() if len(v) >= 3}
        changed = info.items() - self._device_info.items()
        self._device_info.update(info)

        rated_kw = regs[DEVICE_INFO_RATED_POWER] / 1000
        if not RATED_POWER_MIN_KW <= rated_kw <= RATED_POWER_MAX_KW:
            # Fall back to the rating in the model name, e.g. "SM-ONYX-UL-6KW".
            match = re.search(r"(\d+(?:\.\d+)?)\s*KW", info.get(CONF_MODEL, ""), re.IGNORECASE)
            _LOGGER.debug("Rated power register implausible (%s W); model name gives %s",
                          regs[DEVICE_INFO_RATED_POWER], match and match.group(1))
            rated_kw = float(match.group(1)) if match else 0
        plausible = RATED_POWER_MIN_KW <= rated_kw <= RATED_POWER_MAX_KW
        if plausible and not self._rated_power_manual and rated_kw != self.rated_power_kw:
            _LOGGER.info("Detected inverter rated power %.1f kW (%s)",
                         rated_kw, info.get(CONF_MODEL, "unknown model"))
            self.rated_power_kw = rated_kw
            changed = True
        if changed:
            self._persist_config()

    async def async_force_refresh(self) -> None:
        """Immediately connect, poll all sensors, and disconnect."""
        await self._async_poll_all()

    @callback
    def async_request_write(
        self,
        name: str,
        address: int,
        value: int,
        uint32: bool = False,
        data_key: str | None = None,
        data_value: Any = None,
    ) -> None:
        """Queue a register write; it is retried in the background until confirmed.

        Returns immediately, so an automation only has to set a value once.
        A newer request for the same register replaces any pending one. On
        confirmation ``data[data_key]`` is set to ``data_value`` and a
        ``solarmax_touch_lan_write_confirmed`` event fires; if the deadline
        passes first, a ``solarmax_touch_lan_write_failed`` event fires and a
        persistent notification is shown.
        """
        self.pending_writes.pop(address, None)
        self.pending_writes[address] = PendingWrite(
            name=name,
            address=address,
            value=int(value),
            uint32=uint32,
            data_key=data_key,
            data_value=value if data_value is None else data_value,
            deadline=dt_util.utcnow() + timedelta(seconds=WRITE_DEADLINE_SECONDS),
        )
        persistent_notification.async_dismiss(
            self.hass, self._write_notification_id(address)
        )
        self._write_wakeup.set()
        if self._write_task is None or self._write_task.done():
            self._write_task = self._entry.async_create_background_task(
                self.hass, self._async_write_worker(), f"{DOMAIN} write queue"
            )
        self._notify_listeners()

    def options_differ(self, options: dict[str, Any]) -> bool:
        """True if the config entry options no longer match what is running."""
        return (
            options.get(CONF_DAILY_CLOUD_SYNC, self._daily_sync) != self._daily_sync
            or options.get(CONF_AUTO_CLOCK_SYNC, False) != self._auto_clock_sync
        )

    @property
    def next_poll_at(self) -> datetime | None:
        return self._next_poll_at

    # ── Inverter clock ────────────────────────────────────────────────────────

    @property
    def inverter_clock(self) -> datetime | None:
        """The inverter's clock as last read, interpreted in HA's time zone."""
        d = self.data
        try:
            year = int(d["clock_year"])
            month, day = int(d["clock_month_day"]) >> 8, int(d["clock_month_day"]) & 0xFF
            hour, minute = int(d["clock_hour_minute"]) >> 8, int(d["clock_hour_minute"]) & 0xFF
            second = int(d["clock_second"]) >> 8
            return datetime(year, month, day, hour, minute, second,
                            tzinfo=dt_util.now().tzinfo)
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def clock_offset_seconds(self) -> float | None:
        clock = self.inverter_clock
        if clock is None or self._clock_read_at is None:
            return None
        return (clock - self._clock_read_at).total_seconds()

    async def async_sync_clock(self) -> bool:
        """Set the inverter clock to HA's local time and confirm it took.

        Retries on connection failures like other writes. Returns True once
        the read-back is within CLOCK_SYNC_TOLERANCE_SECONDS.
        """
        for attempt, delay in enumerate((0, 5, 15, 30), start=1):
            if delay:
                await asyncio.sleep(delay)
            async with self._poll_lock:
                try:
                    await self.client.async_connect(attempts=WRITE_CONNECT_ATTEMPTS)
                    now = dt_util.now()
                    await self.client.async_write_block(CLOCK_REGISTER, [
                        now.year,
                        (now.month << 8) | now.day,
                        (now.hour << 8) | now.minute,
                        now.second << 8,
                    ])
                    regs = await self.client.async_read_block(CLOCK_REGISTER, 4)
                    self._update_data({
                        "clock_year": regs[0], "clock_month_day": regs[1],
                        "clock_hour_minute": regs[2], "clock_second": regs[3],
                    })
                except ModbusConnectionError as err:
                    _LOGGER.warning("Clock sync attempt %d failed: %s", attempt, err)
                    continue
                finally:
                    await self.client.async_disconnect()
            offset = self.clock_offset_seconds
            self._notify_listeners()
            if offset is not None and abs(offset) <= CLOCK_SYNC_TOLERANCE_SECONDS:
                _LOGGER.info("Inverter clock set to %s", self.inverter_clock)
                return True
            _LOGGER.warning("Inverter clock still off by %s s after sync attempt %d", offset, attempt)
        _LOGGER.error("Could not set the inverter clock")
        return False

    async def _async_daily_clock_sync(self, _now=None) -> None:
        if not self._cloud_sync_active:
            await self.async_sync_clock()

    def pending_value(self, address: int) -> Any:
        """The value waiting to be written to ``address``, if any."""
        pending = self.pending_writes.get(address)
        return pending.data_value if pending else None

    async def _async_write_worker(self) -> None:
        """Drain the write queue, reconnecting with backoff until all confirm or expire."""
        cycle = 0
        while self.pending_writes:
            self._write_wakeup.clear()
            if self._cloud_sync_active:
                # The inverter is reserved for the cloud upload; try again later.
                delay = WRITE_CYCLE_BACKOFF_SECONDS[-1]
            else:
                if await self._async_write_cycle():
                    self._begin_post_write_burst()
                self._expire_pending_writes()
                if not self.pending_writes:
                    break
                delay = WRITE_CYCLE_BACKOFF_SECONDS[
                    min(cycle, len(WRITE_CYCLE_BACKOFF_SECONDS) - 1)
                ]
                cycle += 1
            # Wake early if a new write is queued.
            try:
                await asyncio.wait_for(self._write_wakeup.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            self._expire_pending_writes()

    async def _async_write_cycle(self) -> bool:
        """Connect once and try every pending write. Returns True if any confirmed."""
        confirmed_any = False
        async with self._poll_lock:
            try:
                await self.client.async_connect(attempts=WRITE_CONNECT_ATTEMPTS)
                for address, pending in list(self.pending_writes.items()):
                    pending.attempts += 1
                    if not await self._async_write_and_verify(pending):
                        continue
                    # Only clear it if no newer value was queued meanwhile.
                    if self.pending_writes.get(address) is pending:
                        del self.pending_writes[address]
                    confirmed_any = True
                    if pending.data_key:
                        self.data[pending.data_key] = pending.data_value
                    _LOGGER.info(
                        "%s set to %s (register %s) confirmed after %d cycle(s)",
                        pending.name, pending.data_value, address, pending.attempts,
                    )
                    self.hass.bus.async_fire(
                        EVENT_WRITE_CONFIRMED,
                        {"name": pending.name, "register": address, "value": pending.data_value},
                    )
                if self.status == STATUS_ERROR:
                    self.status = self._idle_status()
            except ModbusConnectionError as err:
                _LOGGER.warning(
                    "Write cycle could not reach the inverter (%d pending): %s",
                    len(self.pending_writes), err,
                )
                self.status = STATUS_ERROR
            finally:
                await self.client.async_disconnect()
        self._notify_listeners()
        return confirmed_any

    async def _async_write_and_verify(self, pending: PendingWrite) -> bool:
        """Write one register on the open connection and read it back.

        Raises ModbusConnectionError if the connection itself is lost, so the
        cycle ends and the remaining writes wait for the next one.
        """
        data_type = "uint32" if pending.uint32 else "uint16"
        for attempt in range(1, WRITE_MAX_ATTEMPTS + 1):
            try:
                if pending.uint32:
                    await self.client.async_write_register_uint32(pending.address, pending.value)
                else:
                    await self.client.async_write_register(pending.address, pending.value)
                await asyncio.sleep(WRITE_VERIFY_SETTLE_SECONDS)
                readback = await self.client.async_read_register(pending.address, data_type)
            except ModbusConnectionError as err:
                if not self.client.connected:
                    raise
                _LOGGER.debug(
                    "%s write attempt %d errored: %s", pending.name, attempt, err
                )
            else:
                if int(readback) == pending.value:
                    return True
                _LOGGER.debug(
                    "%s write not applied (wrote %s, read back %s)",
                    pending.name, pending.value, readback,
                )
            if attempt < WRITE_MAX_ATTEMPTS:
                await asyncio.sleep(WRITE_RETRY_DELAY_SECONDS)
        return False

    @callback
    def _expire_pending_writes(self) -> None:
        now = dt_util.utcnow()
        for address, pending in list(self.pending_writes.items()):
            if pending.deadline > now:
                continue
            del self.pending_writes[address]
            _LOGGER.error(
                "%s could not be set to %s (register %s) after %d cycle(s) over %d s",
                pending.name, pending.data_value, address, pending.attempts,
                WRITE_DEADLINE_SECONDS,
            )
            self.hass.bus.async_fire(
                EVENT_WRITE_FAILED,
                {"name": pending.name, "register": address, "value": pending.data_value},
            )
            persistent_notification.async_create(
                self.hass,
                f"Could not set **{pending.name}** to **{pending.data_value}**: the "
                f"inverter did not confirm the change within "
                f"{WRITE_DEADLINE_SECONDS // 60} minutes. Check that it is reachable "
                f"and try again.",
                title="SolarTouch LAN write failed",
                notification_id=self._write_notification_id(address),
            )
            self._notify_listeners()

    def _write_notification_id(self, address: int) -> str:
        return f"{DOMAIN}_{self._entry.entry_id}_write_{address}"

    def _begin_post_write_burst(self) -> None:
        """Enter a short fast-polling window after a register write.

        Makes the effect of a setting change visible right away. In Live mode
        polling is already fast, so just trigger an immediate poll; in Standby
        or Periodic mode, switch to fast session polling for
        ``WRITE_LINGER_SECONDS`` then restore the mode's normal polling.
        """
        if self._cloud_sync_active:
            return
        if self._mode == CONNECTION_MODE_LIVE:
            self.hass.async_create_task(self._async_poll_all())
            return
        self._stop_polling()
        self._start_session_polling()  # 5 s fast poll + an immediate full poll
        self._reset_live_timer(WRITE_LINGER_SECONDS)
        self.status = STATUS_LIVE
        self._notify_listeners()

    def register_listener(self, callback_fn, countdown: bool = False) -> None:
        self._listeners.append(callback_fn)
        if countdown:
            self._countdown_listeners.append(callback_fn)

    def unregister_listener(self, callback_fn) -> None:
        for listeners in (self._listeners, self._countdown_listeners):
            if callback_fn in listeners:
                listeners.remove(callback_fn)

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
        for cb in self._countdown_listeners:
            cb()

    async def _async_live_poll_tick(self, _now=None) -> None:
        """Live mode tick: connect → fast sensors (+ slow every 10th) → disconnect."""
        if self._cloud_sync_active:
            return
        async with self._poll_lock:
            self._poll_counter += 1
            include_slow = (self._poll_counter % SLOW_POLL_RATIO == 0)
            self._next_poll_at = dt_util.utcnow() + timedelta(seconds=SCAN_FAST_SECONDS)
            try:
                await self.client.async_connect(attempts=POLL_CONNECT_ATTEMPTS)
                new_data = await self.client.async_read_registers_batch(self._fast_defs)
                if include_slow:
                    slow_data = await self.client.async_read_registers_batch(self._slow_defs)
                    new_data.update(slow_data)
                self._update_data(new_data)
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
                await self.client.async_connect(attempts=POLL_CONNECT_ATTEMPTS)
                new_data = await self.client.async_read_registers_batch(self._fast_defs)
                self._update_data(new_data)
                self.last_poll = dt_util.utcnow()
                if self.status == STATUS_ERROR:
                    self.status = STATUS_LIVE
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
                await self.client.async_connect(attempts=POLL_CONNECT_ATTEMPTS)
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
        self._update_data({**fast, **slow})

    def _update_data(self, new_data: dict[str, Any]) -> None:
        """Merge a poll result into ``data``, dropping lifetime-counter glitches.

        Single-poll garbage on a TOTAL_INCREASING sensor is especially harmful:
        HA treats the drop back to the real value as a meter reset and adds
        the spike to the long-term energy statistics permanently.
        """
        if "clock_hour_minute" in new_data:
            self._clock_read_at = dt_util.now()
        for key, value in new_data.items():
            prev = self.data.get(key)
            if key not in self._counter_keys or prev is None:
                self.data[key] = value
                continue
            if 0 <= value - prev <= COUNTER_MAX_STEP:
                self._counter_suspects.pop(key, None)
                self.data[key] = value
                continue
            # Suspicious: accept only if the same new level keeps coming back.
            suspect, count = self._counter_suspects.get(key, (None, 0))
            if suspect is not None and abs(value - suspect) <= COUNTER_MAX_STEP:
                count += 1
            else:
                count = 1
            if count >= COUNTER_CONFIRM_READINGS:
                _LOGGER.warning(
                    "%s changed from %s to %s and stayed there; accepting new level",
                    key, prev, value,
                )
                self._counter_suspects.pop(key, None)
                self.data[key] = value
            else:
                _LOGGER.debug(
                    "Ignoring suspicious %s reading %s (previous %s)", key, value, prev
                )
                self._counter_suspects[key] = (value, count)

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
        await self._async_disconnect_when_idle()
        # Restore the mode's normal polling. After a Periodic-mode write burst
        # this resumes periodic polling; in Standby (e.g. Go Live ending) it
        # stays idle.
        if self._mode == CONNECTION_MODE_LIVE:
            self._start_live_polling()
        elif self._mode == CONNECTION_MODE_PERIODIC:
            self._start_periodic_polling()
        self.status = self._idle_status()
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
        await self._async_disconnect_when_idle()
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

    async def _async_disconnect_when_idle(self) -> None:
        """Close the connection once any running poll or write has finished.

        Closing it mid-poll aborted the poll, which then left the status at
        "error" even though nothing was wrong (and in Standby nothing would
        clear it again).
        """
        async with self._poll_lock:
            await self.client.async_disconnect()

    @callback
    def _notify_listeners(self) -> None:
        for cb in self._listeners:
            cb()
