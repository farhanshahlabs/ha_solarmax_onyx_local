"""Async Modbus TCP client wrapper for SolarTouch LAN."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

_LOGGER = logging.getLogger(__name__)


class ModbusConnectionError(Exception):
    """Raised when the Modbus TCP connection fails."""


def _supports_slave_kwarg(client: AsyncModbusTcpClient) -> bool:
    """Return True if this pymodbus version accepts 'slave' as a keyword arg."""
    try:
        sig = inspect.signature(client.read_holding_registers)
        return "slave" in sig.parameters
    except (ValueError, TypeError):
        return False


class SolarMaxModbusClient:
    """Thin async wrapper around pymodbus AsyncModbusTcpClient."""

    def __init__(self, host: str, port: int, slave: int) -> None:
        self._host = host
        self._port = port
        self._slave = slave
        self._client: AsyncModbusTcpClient | None = None
        self._lock = asyncio.Lock()
        self._slave_kwarg: bool | None = None  # detected on first connect

    async def async_connect(self) -> None:
        async with self._lock:
            if self._client and self._client.connected:
                return
            self._client = AsyncModbusTcpClient(self._host, port=self._port)
            connected = await self._client.connect()
            if not connected:
                self._client = None
                raise ModbusConnectionError(
                    f"Cannot connect to {self._host}:{self._port}"
                )
            # Detect once which calling convention this pymodbus version uses
            self._slave_kwarg = _supports_slave_kwarg(self._client)
            _LOGGER.debug("pymodbus slave kwarg supported: %s", self._slave_kwarg)

    async def async_disconnect(self) -> None:
        async with self._lock:
            if self._client:
                self._client.close()
                self._client = None

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.connected

    async def _read_registers(self, address: int, count: int):
        """Call read_holding_registers with the right signature for this pymodbus version."""
        if self._slave_kwarg:
            return await self._client.read_holding_registers(
                address, count=count, slave=self._slave
            )
        # Newer pymodbus: slave not accepted, only address + count
        return await self._client.read_holding_registers(address, count=count)

    async def _write_register(self, address: int, value: int):
        if self._slave_kwarg:
            return await self._client.write_register(address, value, slave=self._slave)
        return await self._client.write_register(address, value)

    async def _write_registers(self, address: int, values: list[int]):
        if self._slave_kwarg:
            return await self._client.write_registers(address, values, slave=self._slave)
        return await self._client.write_registers(address, values)

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
            except ModbusException as err:
                raise ModbusConnectionError(str(err)) from err

        if result.isError():
            raise ModbusConnectionError(
                f"Modbus error reading register {address}: {result}"
            )
        return _decode(result.registers, data_type, swap)

    async def async_read_registers_batch(
        self, definitions: list[dict]
    ) -> dict[str, Any]:
        """Read multiple registers grouped into contiguous batches for efficiency."""
        results: dict[str, Any] = {}
        sorted_defs = sorted(definitions, key=lambda d: d["register"])

        batches: list[list[dict]] = []
        current: list[dict] = []
        for defn in sorted_defs:
            if not current:
                current.append(defn)
            else:
                gap = defn["register"] - (
                    current[-1]["register"] + (2 if current[-1]["data_type"] in ("uint32", "int32") else 1)
                )
                if gap <= 10:
                    current.append(defn)
                else:
                    batches.append(current)
                    current = [defn]
        if current:
            batches.append(current)

        for batch in batches:
            first_addr = batch[0]["register"]
            last = batch[-1]
            last_addr = last["register"] + (2 if last["data_type"] in ("uint32", "int32") else 1)
            count = last_addr - first_addr

            async with self._lock:
                if not self.connected:
                    raise ModbusConnectionError("Not connected")
                try:
                    result = await self._read_registers(first_addr, count)
                except ModbusException as err:
                    raise ModbusConnectionError(str(err)) from err

            if result.isError():
                _LOGGER.warning("Modbus batch read error at %s: %s", first_addr, result)
                continue

            regs = result.registers
            for defn in batch:
                offset = defn["register"] - first_addr
                count_r = 2 if defn["data_type"] in ("uint32", "int32") else 1
                chunk = regs[offset: offset + count_r]
                if len(chunk) < count_r:
                    continue
                try:
                    raw = _decode(chunk, defn["data_type"], defn.get("swap", False))
                    results[defn["key"]] = round(raw * defn["scale"], 3)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Decode error for %s: %s", defn["key"], err)

        return results

    async def async_write_register(self, address: int, value: int) -> None:
        """Write a single 16-bit holding register."""
        async with self._lock:
            if not self.connected:
                raise ModbusConnectionError("Not connected")
            try:
                result = await self._write_register(address, value)
            except ModbusException as err:
                raise ModbusConnectionError(str(err)) from err
        if result.isError():
            raise ModbusConnectionError(
                f"Modbus write error at register {address}: {result}"
            )

    async def async_write_register_uint32(self, address: int, value: int) -> None:
        """Write a 32-bit value as two consecutive 16-bit registers (high word first)."""
        high = (value >> 16) & 0xFFFF
        low = value & 0xFFFF
        async with self._lock:
            if not self.connected:
                raise ModbusConnectionError("Not connected")
            try:
                result = await self._write_registers(address, [high, low])
            except ModbusException as err:
                raise ModbusConnectionError(str(err)) from err
        if result.isError():
            raise ModbusConnectionError(
                f"Modbus uint32 write error at register {address}: {result}"
            )


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
