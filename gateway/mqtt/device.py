import json
import logging
import asyncio


class HassMqttDevice:
    def __init__(self, id, config, mqtt, ble):
        self._id = id
        self._config = config
        self._mqtt = mqtt
        self._ble = ble

    def __str__(self):
        return f"{self._id} ({self._config.optional('mac')})"

    @property
    def id(self):
        return self._id

    @property
    def component(self):
        return None

    async def listen(self):
        """
        Listen for incoming messages
        """

        # send device configuration for MQTT discovery
        await self.config()

        # listen for incoming MQTT messages
        async with self._mqtt.filtered_messages(self) as messages:
            async for message in messages:
                logging.debug(
                    f"Received message on {message.topic}:\n{message.payload}"
                )

                # get command from topic and load message
                command = message.topic.split("/")[-1]
                payload = None

                try:
                    # get handler from command name
                    handler = getattr(self, f"_mqtt_{command}")
                except AttributeError:
                    logging.debug(f"Missing handler for command {command}")
                    continue

                try:
                    if message.payload:
                        # TODO: let device specify message format (i.e. JSON)
                        payload = message.payload.decode()

                    await handler(payload)
                except asyncio.CancelledError:
                    # ensure cancellation is not swallowed
                    raise
                except:
                    logging.debug(f"Failed to handle message {command}")
                    continue

    async def poll(self):
        """
        Implement polling
        """
        pass

    async def setup(self):
        """
        Prepare device prior to connecting
        """
        pass

    async def config(self):
        """
        Send discovery message
        """
        pass
