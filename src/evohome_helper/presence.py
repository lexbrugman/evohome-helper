import logging

from typing import Any

from evohome_helper.homeassistant import HomeAssistantClient
from settings import Settings

logger = logging.getLogger(__name__)


class PresenceTracker:
    def __init__(self, homeassistant: HomeAssistantClient, settings: Settings):
        self._homeassistant = homeassistant
        self._settings = settings
        self._last_known_presence_state: dict[str, dict[str, Any]] = {}

    async def is_someone_home(self) -> bool:
        for entity_id in self._settings.homeassistant_presence_entities:
            data = await self._get_data(entity_id)
            if data is not None and data.get("is_someone_home"):
                return True

        return False

    async def is_in_away_grace_period(self) -> bool:
        for entity_id in self._settings.homeassistant_presence_entities:
            data = await self._get_data(entity_id)
            if data is None:
                continue

            seconds_since_last_seen = data.get("seconds_since_last_seen")
            if seconds_since_last_seen is not None and seconds_since_last_seen <= self._settings.presence_last_home_grace_time:
                return True

        return False

    def is_presence_known(self) -> bool:
        # whether we have any presence reading (fresh or previously cached) to act on;
        # relies on the cache populated by is_someone_home() earlier in the same cycle
        return any(
            self._last_known_presence_state.get(entity_id) is not None
            for entity_id in self._settings.homeassistant_presence_entities
        )

    async def _get_data(self, entity_id: str) -> dict | None:
        entity_state = await self._homeassistant.get_entity_state(entity_id)
        if entity_state is not None:
            attributes = entity_state.get("attributes", {})

            self._last_known_presence_state[entity_id] = {
                "is_someone_home": entity_state.get("state") == "home",
                "seconds_since_last_seen": attributes.get("seconds_since_last_seen"),
            }

        # None when this entity has never been read successfully: unknown, NOT "away" --
        # fabricating an away reading here would let an HA outage turn the heating down
        return self._last_known_presence_state.get(entity_id)
