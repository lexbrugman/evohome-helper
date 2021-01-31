import logging
import requests
import settings

from evohome_helper.func_tools import return_cache

logger = logging.getLogger(__name__)


@return_cache(refresh_interval=1800)
def get_temperature_info():
    url = f"{settings.HOMEASSISTANT_URL}/api/states/{settings.HOMEASSISTANT_WEATHER_ENTITY}"
    headers = {
        "Authorization": f"Bearer {settings.HOMEASSISTANT_TOKEN}",
        "content-type": "application/json",
    }

    current_temperature = -99

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=5,
        )
        if response.ok:
            weather_data = response.json().get("attributes", {})
            current_temperature = weather_data.get("temperature", current_temperature)
    except Exception:
        logger.exception("failed getting weather information")

    return round(current_temperature, 1)
