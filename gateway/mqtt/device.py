import json
import logging
import asyncio


class HassMqttDevice:
    def __init__(self, id, config, messenger):
        self._id = id
        self._config = config
        self._messenger = messenger

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
        async with self._messenger.filtered_messages(self) as messages:
            async for message in messages:
                logging.debug(
                    f"Received message on {message.topic}:\n{message.payload}"
                )

                # get command from topic and load message
                command = message.topic.split("/")[-1]
                payload = json.loads(message.payload.decode())

                try:
                    # get handler from command name
                    handler = getattr(self, f"_mqtt_{command}")
                except:
                    logging.warning(f"Missing handler for command {command}")
                    continue

                await handler(payload)

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
