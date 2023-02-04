import logging
import requests
import settings

from evohome_helper.func_tools import return_cache

logger = logging.getLogger(__name__)
last_known_presence_state = {}


def is_someone_home():
    for entity_id in settings.HOMEASSISTANT_PRESENCE_ENTITIES:
        if _get_data(entity_id).get("is_someone_home"):
            return True

    return False


def is_in_away_grace_period():
    for entity_id in settings.HOMEASSISTANT_PRESENCE_ENTITIES:
        seconds_since_last_seen = _get_data(entity_id).get("seconds_since_last_seen")
        if seconds_since_last_seen and seconds_since_last_seen <= settings.LAST_HOME_GRACE_TIME:
            return True

    return False


@return_cache(refresh_interval=60)
def _get_data(entity_id):
    url = f"{settings.HOMEASSISTANT_URL}/api/states/{entity_id}"

    try:
        response = requests.get(
            url,
            headers=_headers(),
            timeout=5,
        )
        if response.ok:
            response_data = response.json()
            attributes = response_data.get("attributes", {})

            someone_home = response_data.get("state") == "home"
            seconds_since_last_seen = attributes.get("seconds_since_last_seen")
            
            last_known_presence_state[entity_id] = {
                "is_someone_home": someone_home,
                "seconds_since_last_seen": seconds_since_last_seen,
            }
    except Exception:
        logger.exception("failed getting presence information")

    return last_known_presence_state.setdefault(entity_id, {
        "is_someone_home": False,
        "seconds_since_last_seen": 0,
    })


def _headers():
    return {
        "Authorization": f"Bearer {settings.HOMEASSISTANT_TOKEN}",
        "content-type": "application/json",
    }
