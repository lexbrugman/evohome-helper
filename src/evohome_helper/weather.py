import logging
import settings

from evohome_helper import homeassistant

logger = logging.getLogger(__name__)


async def get_current_temperature() -> float | None:
    ha_weather_entity = settings.HOMEASSISTANT_AUTO_ECO_WEATHER_ENTITY
    if ha_weather_entity is None:
        return None

    entity_state = await homeassistant.get_entity_state(ha_weather_entity)
    if entity_state is None:
        return None

    current_temperature = entity_state.get("attributes", {}).get("temperature")
    if current_temperature is None:
        return None

    return round(current_temperature, 1)
