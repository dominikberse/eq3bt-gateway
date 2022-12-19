import logging
import asyncio


class RetryMixin:
    async def _retry(
        self,
        callback,
        log,
        retries=3,
        fallback=None,
        raise_exception=True,
        args=[],
        kwargs={},
    ):
        """
        Shorthand for reliable retries
        """

        logging.debug(f"{log}...")

        for i in range(retries):
            try:
                await callback(*args, **kwargs)
                return True
            except asyncio.CancelledError:
                # ensure cancellation is not swallowed
                raise
            except:
                logging.exception(f"{log} failed (retry: {i})")

        logging.error(f"{log} not successful")

        if fallback is not None and fallback():
            return True

        if raise_exception:
            raise Exception(f"{log} failed")

        return False
