import logging
import struct
import pexpect
import asyncio
import sys

from datetime import datetime

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

from mqtt import HassMqttDevice
from tools import Ble


class Device(HassMqttDevice):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # validate device data
        self._ble = Ble(self._config.require("mac"))
        self._pass = self._config.optional("pass")

        # retained data
        self._temp = EQ3BT_MIN_TEMP

        # listening event
        self._event = asyncio.Event()
        self._message = None

    @property
    def component(self):
        return "climate"

    def _valid_temp(self, temp):
        return temp > EQ3BT_MIN_TEMP and temp < EQ3BT_MAX_TEMP

    async def _retry(self, callback, log, *args, retries=3, **kwargs):
        """
        Shorthand for retries
        """

        logging.debug(f"{log}...")

        for i in range(retries):
            try:
                await callback(*args, **kwargs)
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
        if data[0] == PROP_INFO_RETURN and data[1] == 1:
            status = Status.parse(data)
            logging.debug("Parsed status: %s", status)

            self._raw_mode = status.mode
            self._valve_state = status.valve
            self._target_temperature = status.target_temp

            if status.mode.BOOST:
                self._mode = Mode.Boost
            elif status.mode.AWAY:
                self._mode = Mode.Away
                self._away_end = status.away
            elif status.mode.MANUAL:
                if status.target_temp == EQ3BT_OFF_TEMP:
                    self._mode = Mode.Closed
                elif status.target_temp == EQ3BT_ON_TEMP:
                    self._mode = Mode.Open
                else:
                    self._mode = Mode.Manual
            else:
                self._mode = Mode.Auto

            presets = status.presets
            if presets:
                self._window_open_temperature = presets.window_open_temp
                self._window_open_time = presets.window_open_time
                self._comfort_temperature = presets.comfort_temp
                self._eco_temperature = presets.eco_temp
                self._temperature_offset = presets.offset
            else:
                self._window_open_temperature = None
                self._window_open_time = None
                self._comfort_temperature = None
                self._eco_temperature = None
                self._temperature_offset = None

            _LOGGER.debug("Valve state:      %s", self._valve_state)
            _LOGGER.debug("Mode:             %s", self.mode_readable)
            _LOGGER.debug("Target temp:      %s", self._target_temperature)
            _LOGGER.debug("Away end:         %s", self._away_end)
            _LOGGER.debug("Window open temp: %s", self._window_open_temperature)
            _LOGGER.debug("Window open time: %s", self._window_open_time)
            _LOGGER.debug("Comfort temp:     %s", self._comfort_temperature)
            _LOGGER.debug("Eco temp:         %s", self._eco_temperature)
            _LOGGER.debug("Temp offset:      %s", self._temperature_offset)

        self._event.set()

    async def _write(self, value):
        async with self._ble as client:
            await client.write_gatt_char(PROP_WRITE_HANDLE - 1, value)

    async def _update(self):
        self._event.clear()
        time = datetime.now()
        value = struct.pack(
            "BBBBBBB",
            PROP_INFO_QUERY,
            time.year % 100,
            time.month,
            time.day,
            time.hour,
            time.minute,
            time.second,
        )

        async with self._ble as client:
            await client.start_notify(PROP_NTFY_HANDLE - 1, self._on_notify)
            await client.write_gatt_char(PROP_WRITE_HANDLE - 1, value)

            await asyncio.wait([self._event.wait()], timeout=10)

    async def setup(self):

        # pair device if required
        if self._pass is not None:
            await self._retry(self._bluetooth_ctl_pair, f"pairing {self}")

    async def config(self):
        await self._retry(self._update, f"updating {self}")

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
            "temperature_command_topic": "~/temperature_set",
            "temperature_state_topic": "~/temperature_state",
            "mode_command_topic": "~/mode_set",
            "mode_state_topic": "~/mode_state",
        }

        await self._messenger.publish(self, "config", message)
        await self._messenger.publish(self, "temperature_state", self._temp)

    async def _mqtt_temperature_set(self, temperature):
        if not self._valid_temp(temperature):
            logging.warning(f"Invalid temperature {temperature}")
            return

        dev_temp = int(temperature * 2)

        # set device mode
        if temperature == EQ3BT_OFF_TEMP or temperature == EQ3BT_ON_TEMP:
            message = struct.pack("BB", PROP_MODE_WRITE, dev_temp | 0x40)

        # set actual temperature
        else:
            message = struct.pack("BB", PROP_TEMPERATURE_WRITE, dev_temp)

        await self._retry(self._write, f"set temp on {self} to {temperature}", message)
