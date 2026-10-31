"""Async Modbus TCP client wrapper for SolarTouch LAN."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

_LOGGER = logging.getLogger(__name__)

# Per-request timeout and pymodbus-level retries for a single read/write.
REQUEST_TIMEOUT_SECONDS = 5
REQUEST_RETRIES = 2

# The inverter's small TCP stack refuses a new connection that arrives right
# after the previous one was closed, so always leave a short gap.
RECONNECT_COOLDOWN_SECONDS = 2.0

# Errors that mean the request never got a usable answer from the inverter.
_TRANSPORT_ERRORS = (ModbusException, OSError, asyncio.TimeoutError)

# Registers closer than this are always read in one request.
SMALL_BATCH_GAP = 10
# Largest block read in one request (the Modbus limit is 125 registers).
MAX_BLOCK_REGISTERS = 100

# Raw register values the inverter returns when a value is not populated yet.
# Checked before scaling so they never reach an entity, whatever its range.
_SENTINELS = {
    "uint16": {0xFFFF},
    "int16": {0x7FFF, -0x8000},
    "uint32": {0xFFFFFFFF},
    "int32": {0x7FFFFFFF, -0x80000000},
}


class ModbusConnectionError(Exception):
    """Raised when the Modbus TCP connection fails."""


class SolarMaxModbusClient:
    """Thin async wrapper around pymodbus AsyncModbusTcpClient."""

    def __init__(self, host: str, port: int, slave: int) -> None:
        self._host = host
        self._port = port
        self._slave = slave
        self._client: AsyncModbusTcpClient | None = None
        self._lock = asyncio.Lock()
        self._last_close: float | None = None
        self._unit_kw: str | None = None   # "device_id" or "slave", see _call
        self.avg_request_seconds: float | None = None
        # (address, count) blocks the inverter refused; read in parts instead.
        self._refused_blocks: set[tuple[int, int]] = set()

    async def async_connect(self, attempts: int = 1, retry_delay: float = 2.0) -> None:
        """Open the connection, retrying up to ``attempts`` times.

        The inverter accepts only one Modbus TCP client and the cloud uploader
        competes for that slot, so a refused connection is usually transient.
        """
        last_err: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                await self._async_connect_once()
                if attempt > 1:
                    _LOGGER.debug("Connected on attempt %d/%d", attempt, attempts)
                return
            except ModbusConnectionError as err:
                last_err = err
                _LOGGER.debug("Connect attempt %d/%d failed: %s", attempt, attempts, err)
            if attempt < attempts:
                await asyncio.sleep(retry_delay * attempt)
        raise ModbusConnectionError(
            f"Cannot connect to {self._host}:{self._port} after {attempts} attempt(s): {last_err}"
        )

    async def _async_connect_once(self) -> None:
        async with self._lock:
            if self._client and self._client.connected:
                return
            if self._client:
                self._close_locked()
            if self._last_close is not None:
                wait = RECONNECT_COOLDOWN_SECONDS - (
                    asyncio.get_running_loop().time() - self._last_close
                )
                if wait > 0:
                    await asyncio.sleep(wait)
            self._client = AsyncModbusTcpClient(
                self._host,
                port=self._port,
                timeout=REQUEST_TIMEOUT_SECONDS,
                retries=REQUEST_RETRIES,
            )
            try:
                connected = await self._client.connect()
            except _TRANSPORT_ERRORS as err:
                self._close_locked()
                raise ModbusConnectionError(str(err)) from err
            if not connected:
                self._close_locked()
                raise ModbusConnectionError(
                    f"Cannot connect to {self._host}:{self._port}"
                )

    async def async_disconnect(self) -> None:
        async with self._lock:
            if self._client:
                self._close_locked()

    def _close_locked(self) -> None:
        try:
            self._client.close()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Error closing Modbus client: %s", err)
        self._client = None
        self._last_close = asyncio.get_running_loop().time()

    @property
    def refused_blocks(self) -> set[tuple[int, int]]:
        """Address blocks the inverter refused as a whole (read in parts instead)."""
        return self._refused_blocks

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.connected

    async def _call(self, method: str, *args, **kwargs):
        """Call a pymodbus request with the unit id under whichever name it accepts.

        pymodbus < 3.10 calls it ``slave``, newer versions ``device_id``. The
        accepted name is remembered after the first call.
        """
        func = getattr(self._client, method)
        names = [self._unit_kw] if self._unit_kw else ["device_id", "slave", None]
        for name in names:
            try:
                if name is None:
                    result = await func(*args, **kwargs)
                else:
                    result = await func(*args, **kwargs, **{name: self._slave})
            except TypeError:
                if name is names[-1]:
                    raise
                continue
            self._unit_kw = name
            return result

    async def _read_registers(self, address: int, count: int):
        return await self._call("read_holding_registers", address, count=count)

    async def _write_register(self, address: int, value: int):
        return await self._call("write_register", address, value)

    async def _write_registers(self, address: int, values: list[int]):
        return await self._call("write_registers", address, values)

    async def async_read_register(
        self, address: int, data_type: str, swap: bool = False
    ) -> float | int:
        """Read one value from holding registers."""
        count = 2 if data_type in ("uint32", "int32") else 1
        async with self._lock:
            if not self.connected:
                raise ModbusConnectionError("Not connected")
            try:
                result = await self._read_registers(address, count)
            except _TRANSPORT_ERRORS as err:
                raise ModbusConnectionError(str(err)) from err

        if result.isError():
            raise ModbusConnectionError(
                f"Modbus error reading register {address}: {result}"
            )
        return _decode(result.registers, data_type, swap)

    async def async_read_registers_batch(
        self, definitions: list[dict]
    ) -> dict[str, Any]:
        """Read many registers using as few requests as possible.

        Each request costs ~0.6-1 s on these inverters, so nearby registers are
        merged into blocks of up to ``MAX_BLOCK_REGISTERS``. Some address ranges
        contain registers the inverter refuses to read, which fails the whole
        block; such blocks are remembered and read as the smaller groups that
        are known to work.
        """
        results: dict[str, Any] = {}
        for group in _merge_batches(_small_batches(definitions)):
            first = group[0][0]["register"]
            count = _span_end(group[-1]) - first
            if len(group) > 1 and (first, count) not in self._refused_blocks:
                regs = await self._async_read_span(first, count)
                if regs is not None:
                    for batch in group:
                        _decode_batch(batch, regs, first, results)
                    continue
                _LOGGER.debug("Inverter refused block %s+%s; reading it in parts", first, count)
                self._refused_blocks.add((first, count))
            for batch in group:
                b_first = batch[0]["register"]
                regs = await self._async_read_span(b_first, _span_end(batch) - b_first)
                if regs is None:
                    _LOGGER.warning("Modbus batch read error at %s", b_first)
                    continue
                _decode_batch(batch, regs, b_first, results)
        return results

    async def _async_read_span(self, address: int, count: int) -> list[int] | None:
        """Raw registers, or None if the inverter answered with an error."""
        async with self._lock:
            if not self.connected:
                raise ModbusConnectionError("Not connected")
            started = asyncio.get_running_loop().time()
            try:
                result = await self._read_registers(address, count)
            except _TRANSPORT_ERRORS as err:
                raise ModbusConnectionError(str(err)) from err
            elapsed = asyncio.get_running_loop().time() - started
            # Exponential moving average, shown in diagnostics.
            self.avg_request_seconds = (
                elapsed if self.avg_request_seconds is None
                else 0.8 * self.avg_request_seconds + 0.2 * elapsed
            )
        if result.isError():
            return None
        return result.registers

    async def async_read_block(self, address: int, count: int) -> list[int]:
        """Read ``count`` raw holding registers starting at ``address``."""
        async with self._lock:
            if not self.connected:
                raise ModbusConnectionError("Not connected")
            try:
                result = await self._read_registers(address, count)
            except _TRANSPORT_ERRORS as err:
                raise ModbusConnectionError(str(err)) from err
        if result.isError():
            raise ModbusConnectionError(
                f"Modbus error reading {count} registers at {address}: {result}"
            )
        return result.registers

    async def async_write_register(self, address: int, value: int) -> None:
        """Write a single 16-bit holding register."""
        async with self._lock:
            if not self.connected:
                raise ModbusConnectionError("Not connected")
            try:
                result = await self._write_register(address, value)
            except _TRANSPORT_ERRORS as err:
                raise ModbusConnectionError(str(err)) from err
        if result.isError():
            raise ModbusConnectionError(
                f"Modbus write error at register {address}: {result}"
            )

    async def async_write_block(self, address: int, values: list[int]) -> None:
        """Write consecutive 16-bit holding registers in one request."""
        async with self._lock:
            if not self.connected:
                raise ModbusConnectionError("Not connected")
            try:
                result = await self._write_registers(address, values)
            except _TRANSPORT_ERRORS as err:
                raise ModbusConnectionError(str(err)) from err
        if result.isError():
            raise ModbusConnectionError(f"Modbus write error at register {address}: {result}")

    async def async_write_register_uint32(self, address: int, value: int) -> None:
        """Write a 32-bit value as two consecutive 16-bit registers (high word first)."""
        high = (value >> 16) & 0xFFFF
        low = value & 0xFFFF
        async with self._lock:
            if not self.connected:
                raise ModbusConnectionError("Not connected")
            try:
                result = await self._write_registers(address, [high, low])
            except _TRANSPORT_ERRORS as err:
                raise ModbusConnectionError(str(err)) from err
        if result.isError():
            raise ModbusConnectionError(
                f"Modbus uint32 write error at register {address}: {result}"
            )


def _width(defn: dict) -> int:
    return 2 if defn["data_type"] in ("uint32", "int32") else 1


def _span_end(batch: list[dict]) -> int:
    """First address after the last register used by ``batch``."""
    return max(d["register"] + _width(d) for d in batch)


def _small_batches(definitions: list[dict]) -> list[list[dict]]:
    """Group registers that are at most ``SMALL_BATCH_GAP`` apart."""
    batches: list[list[dict]] = []
    for defn in sorted(definitions, key=lambda d: d["register"]):
        if batches and defn["register"] - _span_end(batches[-1]) <= SMALL_BATCH_GAP:
            batches[-1].append(defn)
        else:
            batches.append([defn])
    return batches


def _merge_batches(batches: list[list[dict]]) -> list[list[list[dict]]]:
    """Combine neighbouring small batches into blocks of at most MAX_BLOCK_REGISTERS."""
    groups: list[list[list[dict]]] = []
    for batch in batches:
        if groups and _span_end(batch) - groups[-1][0][0]["register"] <= MAX_BLOCK_REGISTERS:
            groups[-1].append(batch)
        else:
            groups.append([batch])
    return groups


def _decode_batch(batch: list[dict], regs: list[int], first_addr: int, results: dict[str, Any]) -> None:
    """Decode, validate and store every definition of ``batch`` from ``regs``."""
    for defn in batch:
        offset = defn["register"] - first_addr
        count_r = _width(defn)
        chunk = regs[offset: offset + count_r]
        if len(chunk) < count_r:
            continue
        try:
            raw = _decode(chunk, defn["data_type"], defn.get("swap", False))
            value = round(raw * defn["scale"], 3)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Decode error for %s: %s", defn["key"], err)
            continue

        if _is_sentinel(chunk, raw, defn["data_type"]):
            _LOGGER.debug("Discarding 'no data' %s reading: raw %s", defn["key"], chunk)
            continue

        # Reject out-of-range readings (bad reads, half-populated registers)
        # so they never spike a chart or the energy statistics.
        lo = defn.get("min")
        hi = defn.get("max")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            _LOGGER.debug(
                "Discarding out-of-range %s reading: %s (valid %s..%s)",
                defn["key"], value, lo, hi,
            )
            continue

        results[defn["key"]] = value


def decode_ascii(regs: list[int]) -> str:
    """Two ASCII characters per register, high byte first; padding dropped."""
    return "".join(
        chr(c) for v in regs for c in (v >> 8, v & 0xFF) if 32 <= c < 127
    ).strip()


def _is_sentinel(regs: list[int], raw: int, data_type: str) -> bool:
    """True if a decoded value is the inverter's "not populated" marker."""
    if raw in _SENTINELS.get(data_type, ()):
        return True
    # An unsigned 32-bit value with a 0xFFFF word is >= ~4.29e9 raw: never a
    # real counter or power reading, but a half-populated register pair.
    return data_type == "uint32" and 0xFFFF in regs


def _decode(regs: list[int], data_type: str, swap: bool) -> int | float:
    if data_type == "uint16":
        return regs[0]
    if data_type == "int16":
        v = regs[0]
        return v - 65536 if v >= 32768 else v
    if data_type in ("uint32", "int32"):
        hi, lo = (regs[1], regs[0]) if swap else (regs[0], regs[1])
        raw = (hi << 16) | lo
        if data_type == "int32" and raw >= 0x80000000:
            raw -= 0x100000000
        return raw
    raise ValueError(f"Unknown data_type: {data_type}")
