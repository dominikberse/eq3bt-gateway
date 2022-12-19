import logging


class State:
    """
    Helper class to track and merge local and remote device state

    This class should be used to improve the experience in case the physical devices
    do not provide a stable connection.
    """

    def __init__(self, prefer_remote=False):
        self._prefer_remote = prefer_remote

        # pending update state
        self._local = {}
        # latest device state
        self._remote = {}

    def __str__(self):
        return f"Remote {self._remote}\nLocal {self._local}\n"

    def merge_remote(self, changes):
        """
        Merge a new device state
        """

        for key, remote in changes.items():
            local = self._local.get(key)
            base = self._remote.get(key)

            # remote state changed without pending update
            if remote != base and local == base:
                self._local[key] = remote
                self._remote[key] = remote

            # remote state changed with pending update
            if remote != base and self._prefer_remote:
                self._local[key] = remote
                self._remote[key] = remote

            # remote state changed according to pending update
            if local == remote:
                self._remote[key] = remote

        logging.debug(f"State merged {changes}\n{self}")

    def push_local(self, local):
        """
        Push a new local change
        """

        # mark local changes as pending updates
        for key, value in local.items():
            if self._local.get(key) != value:
                self._local[key] = value

        logging.debug(f"State pushed {local}\n{self}")

    def get_patch(self):
        """
        Get the changes that are sent to the device
        """

        patch = {}

        # get all pending updates
        for key, local in self._local.items():
            if local != self._remote.get(key):
                patch[key] = local

        logging.debug(f"State patching {patch}\n{self}")

        return patch

    def remote(self, key):
        return self._remote.get(key)

    def local(self, key):
        return self._local.get(key)
