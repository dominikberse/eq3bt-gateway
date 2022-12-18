import logging
import struct
import pexpect
import asyncio
import sys

from datetime import datetime

from eq3bt.eq3btsmart import Thermostat, Mode
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
        self._availability = False
        self._temperature = None

        # listening event
        self._ready = asyncio.Event()
        self._event = asyncio.Event()
        self._message = None

    @property
    def component(self):
        return "climate"

    def set_availability(self, value):
        if value == self._availability:
            return

        logging.info(f"{self} availability: {value}")
        self._availability = value

    async def _bluetooth_ctl_pair(self):
        """
        Hacky method to automatically pair a device
        """

        p = pexpect.spawn("bluetoothctl", encoding="utf-8")

        if logging.getLogger().level <= logging.DEBUG:
            # log to console if debugging
            p.logfile_read = sys.stdout

        deleted = [f"DEL.*{self._ble.address}", f"{self._ble.address} not available"]
        detected = [f"NEW.*{self._ble.address}"]
        passkey = ["Enter passkey.*:"]
        paired = ["Paired: yes", "Failed to pair"]
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
            if p.expect(passkey, timeout=10) != 0:
                raise Exception("Passkey not accepted")
            p.sendline(self._pass)
            if p.expect(paired, timeout=10) != 0:
                raise Exception("Failed to pair")
            p.sendline(f"trust {self._ble.address}")
            if p.expect(trusted, timeout=10) != 0:
                raise Exception("Failed to trust")

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
                "name": self._config.optional("name"),
                "manufacturer": "Equiva",
                "model": "eQ-3 Bluetooth Smart",
                "connections": [["mac", self._ble.address]],
                "identifiers": self._ble.address,
            },
            "modes": ["off", "heat", "auto"],
            "preset_modes": ["boost"],
            "temperature_command_topic": "~/temperature_set",
            "temperature_state_topic": "~/temperature_state",
            "mode_command_topic": "~/mode_set",
            "mode_state_topic": "~/mode_state",
            "availability_topic": "~/available",
            "payload_available": "True",
            "payload_not_available": "False",
            "min_temp": EQ3BT_MIN_TEMP,
            "max_temp": EQ3BT_MAX_TEMP,
            "precision": 0.5,
        }

        await self._messenger.publish(self, "config", message)
        await self._update()

        self._ready.set()

    async def poll(self):
        if self._polling is None:
            logging.info(f"{self} not polling")
            return

        await self._ready.wait()
        logging.info(f"{self} polling every {self._polling}s")

        while True:
            try:
                await asyncio.sleep(self._polling)
                logging.debug(f"{self} polling new state")
                await self._update()
            except asyncio.CancelledError:
                raise
            except:
                # suppress error and continue polling
                logging.exception(f"Exception in polling loop")

    async def _update(self):

        # generate message
        self._thermostat.update()

        # send message
        available = await self._retry(
            self._query,
            f"Update {self}",
            raise_exception=False,
            args=[self._message],
        )

        # update settings if device is available again
        # currently server settings have priority
        # TODO: write some kind of state tracking system
        if available and not self._availability:
            if self._temperature:
                self._mqtt_temperature_set(self._temperature)

        self.set_availability(available)

        # check if this is the first time the state is retrieved
        # store current state as application state
        if self._availability:
            if self._temperature is None:
                self._temperature = self._thermostat.target_temperature

        # push new state
        await self._publish_device_state(self._thermostat.target_temperature)

    async def _mqtt_temperature_set(self, temperature):
        logging.info(f"Setting temp to {temperature}")

        self._temperature = temperature

        # generate message
        self._thermostat.target_temperature = temperature

        # send message
        self.set_availability(
            await self._retry(
                self._write,
                f"Set temp on {self} to {temperature}",
                raise_exception=False,
                args=[self._message],
            )
        )

        # push new state
        await self._publish_device_state(temperature)

    async def _publish_device_state(self, temperature):
        if not self._availability or temperature == Mode.Unknown:
            # deny availability if temperature has not been read
            await self._messenger.publish(self, "available", False)
            return

        if temperature == EQ3BT_MIN_TEMP:
            await self._messenger.publish(self, "mode_state", "off")
        else:
            await self._messenger.publish(self, "mode_state", "heat")

        await self._messenger.publish(self, "temperature_state", temperature)
        await self._messenger.publish(self, "available", True)
