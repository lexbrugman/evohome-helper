import aiohttp
import logging
import settings

logger = logging.getLogger(__name__)


async def get_current_temperature() -> float:
    ha_weather_entity = settings.HOMEASSISTANT_AUTO_ECO_WEATHER_ENTITY
    if ha_weather_entity is None:
        return -99

    current_temperature = -99

    try:
        url = f"{settings.HOMEASSISTANT_URL}/api/states/{ha_weather_entity}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=5)) as response:
                response.raise_for_status()
                response_data = await response.json()
                weather_data = response_data.get("attributes", {})
                current_temperature = weather_data.get("temperature", current_temperature)
    except Exception:
        logger.exception("failed getting weather information")

    return round(current_temperature, 1)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.HOMEASSISTANT_TOKEN}",
        "content-type": "application/json",
    }
