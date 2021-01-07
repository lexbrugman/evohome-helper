import logging
import requests
import settings

logger = logging.getLogger(__name__)


def is_someone_home():
    url = f"{settings.HOMEASSISTANT_URL}/api/states/{settings.HOMEASSISTANT_PRESENCE_ENTITY}"
    headers = {
        "Authorization": f"Bearer {settings.HOMEASSISTANT_TOKEN}",
        "content-type": "application/json",
    }

    someone_home = True

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=5,
        )
        if response.ok:
            someone_home = response.json().get("state") == "home"
    except Exception:
        logger.exception("failed getting presence information")

    return someone_home
