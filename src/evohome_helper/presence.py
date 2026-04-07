import aiohttp
import logging
import settings

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
    url = f"{settings.HOMEASSISTANT_URL}/api/states/{entity_id}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=5)) as response:
                response.raise_for_status()
                response_data = await response.json()
                attributes = response_data.get("attributes", {})

                last_known_presence_state[entity_id] = {
                    "is_someone_home": response_data.get("state") == "home",
                    "seconds_since_last_seen": attributes.get("seconds_since_last_seen"),
                }
    except Exception:
        logger.exception("failed getting presence information")

    return last_known_presence_state.setdefault(entity_id, {
        "is_someone_home": False,
        "seconds_since_last_seen": 0,
    })


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.HOMEASSISTANT_TOKEN}",
        "content-type": "application/json",
    }
