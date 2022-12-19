import asyncio
import logging

from bleak import BleakScanner


logging.getLogger("bleak.backends").setLevel(logging.INFO)


class BleManager:
    """
    Manages BLE connections and access to the BLE stack

    Unifies the scanning process. If a connection is established to a physical device
    and the MAC address has not yet been scanned, a new scanning process is started.
    During that process, the connection data for all known connections is updated.
    """

    def __init__(self):
        self._registry = {}
        self._detected = None

        # python 3.9 and below must create semaphore on asyncio main loop
        self._semaphore = asyncio.Semaphore(1)
        self._event = asyncio.Event()

    @property
    def semaphore(self):
        """
        Manually acquire access to the BLE stack
        """
        return self._semaphore

    def _scanner_callback(self, handle, advertising_data):
        logging.debug(f"Detected {handle}")

        connection = self._registry.get(handle.address)
        if connection is not None:
            if handle.address not in self._detected:
                self._detected.add(handle.address)
                connection._handle = handle

                logging.info(
                    f"Found {handle} ({len(self._detected)} of {len(self._registry)})"
                )

            # notify if all devices were found
            if len(self._detected) == len(self._registry):
                self._event.set()

    async def _unsafe_discover(self, timeout=15.0):
        logging.info("Scanner on")

        self._detected = set()
        self._event.clear()

        async with BleakScanner(self._scanner_callback) as scanner:
            await asyncio.wait([self._event.wait()], timeout=timeout)

        logging.info("Scanner off")

    async def discover(self, *args, **kwargs):
        """
        Manually trigger the scanning process
        """
        async with self._semaphore:
            await self._unsafe_discover(*args, **kwargs)

    def register(self, connection):
        """
        Register a device so that it is recognized when scanning
        """
        self._registry[connection.address] = connection

        logging.info(f"Registered {connection.address}")
