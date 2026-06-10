import aiohttp
import logging
import settings

logger = logging.getLogger(__name__)
_session: aiohttp.ClientSession | None = None


async def get_entity_state(entity_id: str) -> dict | None:
    url = f"{settings.HOMEASSISTANT_URL}/api/states/{entity_id}"

    try:
        async with _get_session().get(url, headers=_headers()) as response:
            response.raise_for_status()
            return await response.json()
    except Exception:
        logger.exception("failed getting the state of entity '%s'", entity_id)
        return None


async def close() -> None:
    global _session

    if _session is not None:
        await _session.close()
        _session = None


def _get_session() -> aiohttp.ClientSession:
    global _session

    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))

    return _session


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.HOMEASSISTANT_TOKEN}",
        "content-type": "application/json",
    }
