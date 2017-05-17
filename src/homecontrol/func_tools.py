import math
import time

from functools import lru_cache
from functools import wraps


def return_cache(refresh_interval=1800, max_size=64, max_retries=6, back_off=1.25, sleep=0.2):
    def function_decorator(f):
        def _get_invalidator():
            return math.floor(time.time() / refresh_interval)

        @lru_cache(maxsize=max_size, typed=True)
        def _cache_call(invalidator, *args, **kwargs):
            data = f(*args, **kwargs)

            num_tries = _get_invalidator() - invalidator
            if not data and num_tries < max_retries:
                sleep_time = sleep * back_off**num_tries
                time.sleep(sleep_time)

                invalidator -= 1

                data = _cache_call(
                    invalidator,
                    *args,
                    **kwargs
                )

            return data

        @wraps(f)
        def function_wrapper(*args, **kwargs):
            invalidator = _get_invalidator()

            return _cache_call(
                invalidator,
                *args,
                **kwargs
            )

        return function_wrapper

    return function_decorator
