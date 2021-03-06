import logging
import requests
import settings

from evohome_helper.func_tools import return_cache

logger = logging.getLogger(__name__)


def is_someone_home():
    for entity_id in settings.HOMEASSISTANT_PRESENCE_ENTITIES:
        if _get_data(entity_id).get("is_someone_home"):
            return True

    return False


def is_in_away_grace_period():
    for entity_id in settings.HOMEASSISTANT_PRESENCE_ENTITIES:
        if _get_data(entity_id).get("seconds_since_last_seen") <= settings.LAST_HOME_GRACE_TIME:
            return True

    return False


@return_cache(refresh_interval=60)
def _get_data(entity_id):
    url = f"{settings.HOMEASSISTANT_URL}/api/states/{entity_id}"

    someone_home = True
    seconds_since_last_seen = 0

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
            seconds_since_last_seen = attributes.get("seconds_since_last_seen", 0)
    except Exception:
        logger.exception("failed getting presence information")

    return {
        "is_someone_home": someone_home,
        "seconds_since_last_seen": seconds_since_last_seen,
    }


def _headers():
    return {
        "Authorization": f"Bearer {settings.HOMEASSISTANT_TOKEN}",
        "content-type": "application/json",
    }
