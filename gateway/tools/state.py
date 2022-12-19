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
        # physical device state
        self._remote = {}
        # latest state
        self._base = {}

    def merge_remote(self, changes):
        """
        Merge a new device state
        """

        for key, remote in changes:
            local = self._local.get(key)
            base = self._base.get(key)

            # remote state changed without pending update
            if remote != base and local == base:
                self._local[key] = remote
                self._base[key] = remote

            # remote state changed with pending update
            if remote != base and self._prefer_remote:
                self._local[key] = remote
                self._base[key] = remote

            # remote state changed according to pending update
            if local == remote:
                self._base[key] = remote

        logging.debug(f"State merged {changes}")

    def push_local(self, local):
        """
        Push a new local change
        """

        # mark local changes as pending updates
        for key, value in local:
            if self._local.get(key) != value:
                self._local[key] = value

        logging.debug(f"State pushed {local}")

    def get_patch(self):
        """
        Get the changes that are sent to the device
        """

        patch = {}

        # get all pending updates
        for key, local in self._local:
            remote = self._remote.get(key)
            base = self._base.get(key)

            # local state changed and overrides device changes
            if local != base and not self._prefer_remote:
                patch[key] = local

            # local state changed without device changes
            if local != base and remote == base:
                patch[key] = local

        logging.debug(f"State patching {patch}")
