import asyncio
import logging
import sys

from contextlib import AsyncExitStack

from bleak import BleakClient


class BleConnection(AsyncExitStack):
    """
    Manages and establishes the connection to a single BLE device
    """

    def __init__(self, address, manager):
        super().__init__()

        self._address = address
        self._handle = None
        self._manager = manager

        # automatically register with manager
        self._manager.register(self)

    @property
    def address(self):
        return self._address

    async def __aenter__(self):
        await super().__aenter__()

        try:

            acquire = asyncio.create_task(
                self.enter_async_context(self._manager.semaphore)
            )
            while True:
                # ensure unique access to BLE
                done, _ = await asyncio.wait([acquire], timeout=10.0)

                if not done:
                    logging.debug(f"{self._address} still waiting for semaphore...")
                else:
                    break

            # manually trigger device discovery
            if self._handle is None:
                await self._manager._unsafe_discover()
            if self._handle is None:
                raise Exception(f"Connection [{self._address}] not available")

            logging.debug(f"Connection [{self._address}] established")

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

    async def __aexit__(self, exc_type, exc, tb):
        await super().__aexit__(exc_type, exc, tb)

    def lost(self):
        logging.warning(f"Lost connection [{self._address}]")

        self._handle = None
