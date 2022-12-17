"""
Constraints BLE access to a single device
"""

import asyncio
import logging
import sys

from contextlib import AsyncExitStack

from bleak import BleakClient, BleakScanner


logging.getLogger("bleak.backends").setLevel(logging.INFO)


class Ble(AsyncExitStack):
    __semaphore = None
    __registry = {}
    __detected = None
    __event = None

    def __init__(self, address, on_connect=None, on_detect=None):
        super().__init__()

        self._address = address
        self._on_connect = on_connect
        self._on_detect = on_detect
        self._handle = None

        # register device
        Ble.__registry[address] = self

        logging.info(f"Registered {self._address}")

    @staticmethod
    def init():

        # python 3.9 and below must create semaphore on asyncio main loop
        Ble.__semaphore = asyncio.Semaphore(1)
        Ble.__event = asyncio.Event()

    @staticmethod
    def _scanner_callback(handle, advertising_data):
        logging.debug(f"Detected {handle}")

        device = Ble.__registry.get(handle.address)
        if device is not None:
            if handle.address not in Ble.__detected:
                Ble.__detected.add(handle.address)
                device._handle = handle

                logging.info(
                    f"Found {handle} ({len(Ble.__detected)} of {len(Ble.__registry)})"
                )

            # notify if all devices were found
            if len(Ble.__detected) == len(Ble.__registry):
                Ble.__event.set()

    @staticmethod
    async def _unsafe_discover(timeout=15.0):
        logging.info("Scanner on")

        Ble.__detected = set()
        Ble.__event.clear()

        async with BleakScanner(Ble._scanner_callback) as scanner:
            await asyncio.wait([Ble.__event.wait()], timeout=timeout)

        logging.info("Scanner off")

        for address in Ble.__detected:
            device = Ble.__registry[address]

            if device._on_detect:
                client = BleakClient(device._handle)
                await device._on_detect(client)

    @staticmethod
    async def discover(*args, **kwargs):
        async with Ble.__semaphore:
            await Ble._unsafe_discover(*args, **kwargs)

    @property
    def address(self):
        return self._address

    async def __aenter__(self):
        await super().__aenter__()

        try:

            acquire = asyncio.create_task(self.enter_async_context(Ble.__semaphore))
            while True:
                # ensure unique access to BLE
                done, _ = await asyncio.wait([acquire], timeout=10.0)

                if not done:
                    logging.warning(f"{self._address} still waiting for semaphore...")
                else:
                    break

            # manually trigger device discovery
            if self._handle is None:
                await Ble._unsafe_discover()
            if self._handle is None:
                raise Exception(f"Device not available ({self._address})")

            # return connection
            return await self.enter_async_context(
                BleakClient(self._handle, timeout=20.0)
            )

        except:

            # ensure cleanup if exception is raised above
            if await self.__aexit__(*sys.exc_info()):
                pass
            else:
                raise

    def lost(self):
        logging.warning(f"Lost connection to {self}")

        self._handle = None
