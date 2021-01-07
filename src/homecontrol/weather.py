import logging

from homecontrol.func_tools import return_cache

logger = logging.getLogger(__name__)


@return_cache(refresh_interval=1800)
def get_temperature_info(location):
    high_temperature = -99
    current_temperature = -99

    return round(high_temperature, 1), round(current_temperature, 1)
