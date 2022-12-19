import logging
import pexpect
import sys


class PairMixin:
    async def _bluetooth_ctl_pair(self):
        """
        Hacky method to automatically pair a device
        """

        p = pexpect.spawn("bluetoothctl", encoding="utf-8")

        if logging.getLogger().level <= logging.DEBUG:
            # log to console if debugging
            p.logfile_read = sys.stdout

        deleted = [f"DEL.*{self._address}", f"{self._address} not available"]
        detected = [f"NEW.*{self._address}"]
        passkey = ["Enter passkey.*:"]
        paired = ["Paired: yes", "Failed to pair"]
        trusted = ["Trusted: yes"]

        # start scanning
        p.sendline()
        p.expect("#")
        p.sendline(f"remove {self._address}")
        p.expect(deleted)
        p.sendline("scan on")

        try:

            # wait for device to be detected
            p.expect(detected, timeout=10)

            # pair and trust device
            p.sendline(f"pair {self._address}")
            if p.expect(passkey, timeout=10) != 0:
                raise Exception("Passkey not accepted")
            p.sendline(self._pass)
            if p.expect(paired, timeout=10) != 0:
                raise Exception("Failed to pair")
            p.sendline(f"trust {self._address}")
            if p.expect(trusted, timeout=10) != 0:
                raise Exception("Failed to trust")

            # disconnect
            p.expect("#")
            p.sendline("disconnect")
            p.expect("#")

        finally:

            p.sendline("quit")
            p.close()
