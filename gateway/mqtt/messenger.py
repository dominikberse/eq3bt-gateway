import json

from asyncio_mqtt.client import Client
from contextlib import AsyncExitStack

from tools import Tasks


class HassMqttMessenger:
    """
    Provides home assistant specific MQTT functionality
    Manages a set of devices and tasks to receive and handle incoming messages.
    """

    def __init__(self, config):
        self._config = config
        self._devices = {}
        self._paths = {}

        self._client = Client(
            self._config.require("mqtt.broker"),
            username=self._config.optional("mqtt.username"),
            password=self._config.optional("mqtt.password"),
        )
        self._topic = config.optional("mqtt.topic", "eq3bt")

    async def __aenter__(self):
        await self._client.__aenter__()
        await self._client.subscribe("homeassistant/#")

        return self

    async def __aexit__(self, *args, **kwargs):
        return await self._client.__aexit__(*args, **kwargs)

    @property
    def client(self):
        return self._client

    @property
    def topic(self):
        return self._topic

    def register(self, device):
        """
        Register a device
        """
        self._devices[device.id] = device

    def device_topic(self, device):
        """
        Return base topic for a specific device
        """
        return f"homeassistant/{device.component}/{self._topic}/{device.id}"

    def filtered_messages(self, device, topic="#"):
        """
        Shorthand to get messages for a specific device
        """
        return self._client.filtered_messages(f"{self.device_topic(device)}/{topic}")

    async def publish(self, device, topic, message, **kwargs):
        """
        Send a state update for a specific nde
        """
        if isinstance(message, dict):
            message = json.dumps(message)

        await self._client.publish(
            f"{self.device_topic(device)}/{topic}",
            str(message).encode(),
            **kwargs,
        )
