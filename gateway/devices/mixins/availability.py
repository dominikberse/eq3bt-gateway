import logging


class AvailabilityMixin:
    def set_availability(self, value):
        """
        Update the device availability

        If the device was not available for AVAILABILITY_RETRIES times,
        mark the device as unavailable in Home Assistant.
        """

        if not value and self._availability == 0:
            return
        if value and self._availability > 0:
            self._availability = self._availability_retries
            return

        if not value:
            self._availability -= 1
        if value:
            self._availability = self._availability_retries

        # availability changed
        logging.info(f"{self} availability: {self._availability > 0}")
