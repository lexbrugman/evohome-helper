import logging
import settings

from evohome_helper import homeassistant

logger = logging.getLogger(__name__)
last_known_presence_state = {}


async def is_someone_home() -> bool:
    for entity_id in settings.HOMEASSISTANT_PRESENCE_ENTITIES:
        data = await _get_data(entity_id)
        if data.get("is_someone_home"):
            return True

    return False


async def is_in_away_grace_period() -> bool:
    for entity_id in settings.HOMEASSISTANT_PRESENCE_ENTITIES:
        data = await _get_data(entity_id)
        seconds_since_last_seen = data.get("seconds_since_last_seen")
        if seconds_since_last_seen is not None and seconds_since_last_seen <= settings.PRESENCE_LAST_HOME_GRACE_TIME:
            return True

    return False


async def _get_data(entity_id: str) -> dict:
    entity_state = await homeassistant.get_entity_state(entity_id)
    if entity_state is not None:
        attributes = entity_state.get("attributes", {})

        last_known_presence_state[entity_id] = {
            "is_someone_home": entity_state.get("state") == "home",
            "seconds_since_last_seen": attributes.get("seconds_since_last_seen"),
        }

    return last_known_presence_state.setdefault(entity_id, {
        "is_someone_home": False,
        "seconds_since_last_seen": 0,
    })
