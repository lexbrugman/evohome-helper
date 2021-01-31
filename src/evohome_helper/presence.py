import logging
import requests
import settings

from evohome_helper.func_tools import return_cache

logger = logging.getLogger(__name__)


def is_someone_home():
    return _get_data().get("is_someone_home")


def is_in_away_grace_period():
    return _get_data().get("seconds_since_last_seen") <= settings.LAST_HOME_GRACE_TIME


@return_cache(refresh_interval=60)
def _get_data():
    url = f"{settings.HOMEASSISTANT_URL}/api/states/{settings.HOMEASSISTANT_PRESENCE_ENTITY}"
    headers = {
        "Authorization": f"Bearer {settings.HOMEASSISTANT_TOKEN}",
        "content-type": "application/json",
    }

    someone_home = True
    seconds_since_last_seen = 0

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=5,
        )
        if response.ok:
            response_data = response.json()
            attributes = response_data.get("attributes", {})

            someone_home = response_data.get("state") == "home"
            seconds_since_last_seen = attributes.get("seconds_since_last_seen")
    except Exception:
        logger.exception("failed getting presence information")

    return {
        "is_someone_home": someone_home,
        "seconds_since_last_seen": seconds_since_last_seen,
    }
