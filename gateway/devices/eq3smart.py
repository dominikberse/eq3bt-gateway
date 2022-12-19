import logging
import asyncio

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
from ble import BleConnection

from tools import State

from .mixins.retry import RetryMixin
from .mixins.availability import AvailabilityMixin
from .mixins.pair import PairMixin


class DummyConnection:
    def __init__(self, address, interface):
        self._device = interface

    def make_request(self, handle, value):
        self._device._message = value

    def set_callback(self, *args, **kwargs):
        pass


class Device(HassMqttDevice, RetryMixin, AvailabilityMixin, PairMixin):
    AVAILABILITY_RETRIES = 5

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # availability mixin
        self._availability_retries = Device.AVAILABILITY_RETRIES
        self._availability = Device.AVAILABILITY_RETRIES

        # validate device data
        self._address = self._config.require("mac")
        self._pass = self._config.optional("pass")
        self._polling = self._config.optional("poll", 300)

        # physical device connection
        self._connection = BleConnection(self._address, self._ble)

        # retained data
        self._thermostat = Thermostat(None, self, DummyConnection)
        self._state = State()

        # listening event
        self._ready = asyncio.Event()
        self._event = asyncio.Event()
        self._message = None

    @property
    def component(self):
        return "climate"

    async def _on_notify(self, characteristic, data):

        # parse message
        self._thermostat.handle_notification(data)

        # notify about received status
        self._event.set()

    async def _write(self, value):
        try:
            async with self._connection as client:
                await client.write_gatt_char(PROP_WRITE_HANDLE - 1, value)
        except BleakDeviceNotFoundError:
            self._connection.lost()
            raise

    async def _query(self, value):
        try:
            async with self._connection as client:
                await client.start_notify(PROP_NTFY_HANDLE - 1, self._on_notify)
                await client.write_gatt_char(PROP_WRITE_HANDLE - 1, value)

                await asyncio.wait([self._event.wait()], timeout=15)
        except BleakDeviceNotFoundError:
            self._connection.lost()
            raise

    async def setup(self):

        # pair device if required
        if self._pass is not None:
            await self._retry(self._bluetooth_ctl_pair, f"Pair {self}")

            logging.info(f"Paired {self}")

    async def config(self):
        message = {
            "~": self._mqtt.device_topic(self),
            "unique_id": self._id,
            "object_id": self._id,
            "name": self._config.optional("name"),
            "device": {
                "name": self._config.optional("name"),
                "manufacturer": "Equiva",
                "model": "eQ-3 Bluetooth Smart",
                "connections": [["mac", self._address]],
                "identifiers": self._address,
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

        await self._mqtt.publish(self, "config", message)
        await self._pull()

        self._ready.set()

    async def poll(self):
        if self._polling is None:
            logging.info(f"{self} not polling")
            return

        await self._ready.wait()
        logging.info(f"{self} polling every {self._polling}s")

        while True:
            # ensure sleep problems are propagated
            await asyncio.sleep(self._polling)

            try:
                logging.debug(f"{self} polling new state")
                await self._pull()
                await self._push()

            except asyncio.CancelledError:
                # graceful exit
                return
            except:
                # suppress error and continue polling
                logging.exception(f"Exception in polling loop")

    async def _pull(self):
        """
        Pull remote state from device
        """

        # generate message
        self._thermostat.update()
        # send message
        success = await self._retry(
            self._query,
            f"Update {self}",
            raise_exception=False,
            args=[self._message],
        )

        self.set_availability(success)

        # merge retrieved state
        if success:
            self._state.merge_remote(
                {
                    "temperature": self._thermostat.target_temperature,
                    "mode": self._thermostat.mode,
                }
            )

        # publish new state to Home Assistant
        await self._publish_device_state()

    async def _push(self):
        patch = self._state.get_patch()

        temperature = patch.get("temperature")
        if temperature is not None:
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

        # publish new state to Home Assistant
        await self._publish_device_state()

    async def _mqtt_temperature_set(self, temperature):
        if temperature == EQ3BT_MIN_TEMP:
            self._state.push_local(
                {
                    "mode": Mode.Closed,
                    "temperature": EQ3BT_MIN_TEMP,
                }
            )
        else:
            self._state.push_local(
                {
                    "mode": Mode.Manual,
                    "temperature": temperature,
                }
            )

        # push device state
        await self._push()

    async def _mqtt_mode_set(self, mode):
        if mode == "off":
            self._state.push_local({"mode": Mode.Closed})
        elif mode == "heat":
            self._state.push_local({"mode": Mode.Manual})
        else:
            raise Exception("Unknown mode")

        # push device state
        await self._push()

    async def _publish_device_state(self):

        # optimistic updates (use local state)
        temperature = self._state.local("temperature")
        mode = self._state.local("mode")

        if (
            not self._availability
            or temperature == Mode.Unknown
            or mode == Mode.Unknown
        ):
            # deny availability
            await self._mqtt.publish(self, "available", False)
            return

        # translate mode
        if self._thermostat.mode == Mode.Closed:
            await self._mqtt.publish(self, "mode_state", "off")
        if self._thermostat.mode == Mode.Auto:
            await self._mqtt.publish(self, "mode_state", "auto")
        if self._thermostat.mode in [Mode.Boost, Mode.Open, Mode.Manual]:
            await self._mqtt.publish(self, "mode_state", "heat")

        await self._mqtt.publish(self, "temperature_state", temperature)
        await self._mqtt.publish(self, "available", True)
