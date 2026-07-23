import logging

from evohome_helper.homeassistant import HomeAssistantClient
from settings import Settings

logger = logging.getLogger(__name__)


class WeatherService:
    def __init__(self, homeassistant: HomeAssistantClient, settings: Settings):
        self._homeassistant = homeassistant
        self._settings = settings

    async def get_current_temperature(self) -> float | None:
        weather_entity = self._settings.homeassistant_auto_eco_weather_entity
        if weather_entity is None:
            return None

        entity_state = await self._homeassistant.get_entity_state(weather_entity)
        if entity_state is None:
            return None

        current_temperature = entity_state.get("attributes", {}).get("temperature")
        if current_temperature is None:
            return None

        return round(current_temperature, 1)
