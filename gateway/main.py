import argparse
import asyncio
import importlib
import logging

from contextlib import AsyncExitStack, suppress

from mqtt import HassMqttMessenger
from ble import BleManager

from tools import Tasks
from tools import Config


logging.basicConfig(level=logging.DEBUG)


async def run(args):
    async with AsyncExitStack() as stack:

        # load config
        config = Config(args.config)

        # initialize BLE connection manager
        ble = BleManager()

        # connect to broker
        tasks = await stack.enter_async_context(Tasks())
        mqtt = await stack.enter_async_context(HassMqttMessenger(config))

        # spawn task for every device
        devices = config.require("devices")
        for id, device_data in devices.items():
            device_config = Config(config=device_data)
            module_name = device_config.require("module")
            try:
                module = importlib.import_module(f"devices.{module_name}")
                device = module.Device(id, device_config, mqtt, ble)

                # device specific setup if required
                await device.setup()

                tasks.spawn(device.listen(), f"device {device}")
                tasks.spawn(device.poll(), f"polling {device}")
            except:
                logging.exception(f"Failed to module {module_name} for {id}")
                return

        # wait for all tasks
        await tasks.gather()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="./config.yaml")
    args = parser.parse_args()

    # run application
    with suppress(KeyboardInterrupt):
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
