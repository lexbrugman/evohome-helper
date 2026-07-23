import logging

import aiohttp

from settings import Settings

logger = logging.getLogger(__name__)


class HomeAssistantClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._session: aiohttp.ClientSession | None = None

    async def get_entity_state(self, entity_id: str) -> dict | None:
        url = f"{self._settings.homeassistant_url}/api/states/{entity_id}"

        try:
            async with self._get_session().get(url, headers=self._headers()) as response:
                response.raise_for_status()
                return await response.json()
        except Exception:
            logger.exception("failed getting the state of entity '%s'", entity_id)
            return None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))

        return self._session

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._settings.homeassistant_token}",
            "content-type": "application/json",
        }
