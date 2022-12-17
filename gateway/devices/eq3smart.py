import logging
import struct
import pexpect
import asyncio
import sys

from datetime import datetime

from eq3bt.eq3btsmart import Thermostat
from eq3bt.structures import Status
from eq3bt.eq3btsmart import (
    PROP_WRITE_HANDLE,
    PROP_NTFY_HANDLE,
    PROP_ID_QUERY,
    PROP_ID_RETURN,
    PROP_INFO_QUERY,
    PROP_INFO_RETURN,
    PROP_COMFORT_ECO_CONFIG,
    PROP_OFFSET,
    PROP_WINDOW_OPEN_CONFIG,
    PROP_SCHEDULE_QUERY,
    PROP_SCHEDULE_RETURN,
    PROP_MODE_WRITE,
    PROP_TEMPERATURE_WRITE,
    PROP_COMFORT,
    PROP_ECO,
    PROP_BOOST,
    PROP_LOCK,
    EQ3BT_AWAY_TEMP,
    EQ3BT_MIN_TEMP,
    EQ3BT_MAX_TEMP,
    EQ3BT_OFF_TEMP,
    EQ3BT_ON_TEMP,
)

from bleak.exc import BleakDeviceNotFoundError

from mqtt import HassMqttDevice
from tools import Ble


class DummyConnection:
    def __init__(self, address, interface):
        self._device = interface

    def make_request(self, handle, value):
        self._device._message = value

    def set_callback(self, *args, **kwargs):
        pass


class Device(HassMqttDevice):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # validate device data
        self._ble = Ble(self._config.require("mac"))
        self._pass = self._config.optional("pass")
        self._polling = self._config.optional("poll", 300)

        # retained data
        self._thermostat = Thermostat(None, self, DummyConnection)

        # listening event
        self._ready = asyncio.Event()
        self._event = asyncio.Event()
        self._message = None

    @property
    def component(self):
        return "climate"

    async def _retry(self, callback, log, *args, retries=3, **kwargs):
        """
        Shorthand for retries
        """

        logging.debug(f"{log}...")

        for i in range(retries):
            try:
                await callback(*args, **kwargs)
                return
            except asyncio.CancelledError:
                raise
            except KeyboardInterrupt:
                return
            except:
                logging.exception(f"{log} failed (retry: {i})")

        logging.error(f"{log} not successful")

    async def _bluetooth_ctl_pair(self):
        """
        Hacky method to automatically pair a device
        """

        p = pexpect.spawn("bluetoothctl", encoding="utf-8")
        p.logfile_read = sys.stdout

        deleted = [f"DEL.*{self._ble.address}", f"{self._ble.address} not available"]
        detected = [f"NEW.*{self._ble.address}"]
        passkey = ["Enter passkey.*:"]
        paired = ["Paired: yes"]
        trusted = ["Trusted: yes"]

        # start scanning
        p.sendline()
        p.expect("#")
        p.sendline(f"remove {self._ble.address}")
        p.expect(deleted)
        p.sendline("scan on")

        try:

            # wait for device to be detected
            p.expect(detected, timeout=10)

            # pair and trust device
            p.sendline(f"pair {self._ble.address}")
            p.expect(passkey, timeout=10)
            p.sendline(self._pass)
            p.expect(paired, timeout=10)
            p.sendline(f"trust {self._ble.address}")
            p.expect(trusted, timeout=10)

            # disconnect
            p.expect("#")
            p.sendline("disconnect")
            p.expect("#")

        finally:

            p.sendline("quit")
            p.close()

    async def _on_notify(self, characteristic, data):

        # parse message
        self._thermostat.handle_notification(data)

        # notify about received status
        self._event.set()

    async def _write(self, value):
        try:
            async with self._ble as client:
                await client.write_gatt_char(PROP_WRITE_HANDLE - 1, value)
        except BleakDeviceNotFoundError:
            self._ble.lost()

    async def _query(self, value):
        try:
            async with self._ble as client:
                await client.start_notify(PROP_NTFY_HANDLE - 1, self._on_notify)
                await client.write_gatt_char(PROP_WRITE_HANDLE - 1, self._message)

                await asyncio.wait([self._event.wait()], timeout=15)
        except BleakDeviceNotFoundError:
            self._ble.lost()

    async def setup(self):

        # pair device if required
        if self._pass is not None:
            await self._retry(self._bluetooth_ctl_pair, f"Pair {self}")

            logging.info(f"Paired {self}")

    async def config(self):
        message = {
            "~": self._messenger.device_topic(self),
            "unique_id": self._id,
            "object_id": self._id,
            "name": self._config.optional("name"),
            "device": {
                "manufacturer": "Equiva",
                "model": "eQ-3 Bluetooth Smart",
                "connections": [["mac", self._ble._address]],
            },
            "modes": ["off", "heat", "auto"],
            "preset_modes": ["boost"],
            "temperature_command_topic": "~/temperature_set",
            "temperature_state_topic": "~/temperature_state",
            "mode_command_topic": "~/mode_set",
            "mode_state_topic": "~/mode_state",
        }

        await self._messenger.publish(self, "config", message)
        self._ready.set()

    async def poll(self):
        if self._polling is None:
            logging.info(f"{self} not polling")
            return

        await self._ready.wait()
        logging.info(f"{self} polling every {self._polling}s")

        try:
            logging.debug(f"{self} polling new state")
            await self._update()
            await asyncio.sleep(self._polling)
        except asyncio.CancelledError:
            raise
        except:
            logging.exception()

    async def _update(self):

        # generate message
        self._thermostat.update()

        # send message
        await self._retry(self._query, f"Update {self}", self._message)

        # push new state
        await self._messenger.publish(
            self, "temperature_state", self._thermostat.target_temperature
        )

    async def _mqtt_temperature_set(self, temperature):
        logging.info(f"Setting temp to {temperature}")

        # generate message
        self._thermostat.target_temperature = temperature

        # send message
        await self._retry(
            self._write, f"Set temp on {self} to {temperature}", self._message
        )

        # push new state
        await self._messenger.publish(self, "temperature_state", temperature)
