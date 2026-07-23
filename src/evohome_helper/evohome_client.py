import asyncio
import json
import logging
import os

from datetime import datetime, timedelta
from typing import Generator

import aiohttp

from evohomeasync2 import ControlSystem, EvohomeClient, Location, SystemMode
from evohomeasync2.auth import AbstractTokenManager
from evohomeasync2.exceptions import ApiCallFailedError, AuthenticationFailedError, BadUserCredentialsError
from tenacity import retry, retry_if_exception_type, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from settings import Settings

logger = logging.getLogger(__name__)

# only retry errors that can resolve on their own; invalid credentials never will
_TRANSIENT_ERRORS = (
    aiohttp.ClientError,
    TimeoutError,
    ApiCallFailedError,
    AuthenticationFailedError,
)
_retry = retry(
    retry=retry_if_exception_type(_TRANSIENT_ERRORS) & retry_if_not_exception_type(BadUserCredentialsError),
    wait=wait_exponential(),
    stop=stop_after_attempt(6),
    reraise=True,
)

# schedules rarely change; refetching them every cycle wastes the vendor's tight API rate limit
_SCHEDULE_REFRESH_INTERVAL = timedelta(hours=1)


class LocationNotFound(Exception):
    def __init__(self, location_name: str):
        super().__init__(f"the location '{location_name}' does not exist in the evohome account")


class _TokenManager(AbstractTokenManager):
    """Caches auth tokens on disk so restarts reuse them instead of
    re-authenticating against the heavily rate-limited vendor API."""

    def __init__(self, username: str, password: str, websession: aiohttp.ClientSession, token_cache_path: str):
        super().__init__(username, password, websession)
        self._token_cache_path = token_cache_path

    async def load_access_token(self) -> None:
        try:
            with open(self._token_cache_path) as f:
                self._import_access_token(json.load(f))
        except FileNotFoundError:
            pass
        except (KeyError, ValueError):
            logger.warning("ignoring the invalid token cache at '%s'", self._token_cache_path)

    async def save_access_token(self) -> None:
        # best-effort and atomic: a failed cache write must never abort a successful
        # authentication (which the library performs before calling this), and must
        # not leave a truncated cache behind
        tmp_path = f"{self._token_cache_path}.tmp"
        try:
            # 0o600: the cache holds a long-lived refresh token, so keep it owner-only
            # (os.replace preserves the temp file's mode on the destination)
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(self._export_access_token(), f)
            os.replace(tmp_path, self._token_cache_path)
        except OSError:
            logger.warning("could not persist the token cache to '%s'", self._token_cache_path)


def get_control_systems(location: Location) -> Generator[ControlSystem, None, None]:
    for gateway in location.gateways:
        for control_system in gateway.systems:
            yield control_system


class EvohomeService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: EvohomeClient | None = None
        self._websession: aiohttp.ClientSession | None = None
        self._schedule_refresh_times: dict[str, datetime] = {}
        # serialize client creation so concurrent callers cannot each open a session
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        self._schedule_refresh_times.clear()
        self._client = None

        if self._websession is not None:
            await self._websession.close()
            self._websession = None

    async def reset(self) -> None:
        # discard the cached client so the next call recreates it from scratch
        await self.close()

    @_retry
    async def _get_client(self) -> EvohomeClient:
        async with self._lock:
            if self._client is None:
                websession = aiohttp.ClientSession()
                try:
                    token_manager = _TokenManager(
                        self._settings.evohome_username,
                        self._settings.evohome_password,
                        websession,
                        self._settings.evohome_token_cache_path,
                    )
                    await token_manager.load_access_token()

                    client = EvohomeClient(token_manager)
                    await client.update(dont_update_status=True)
                except BaseException:
                    await websession.close()
                    raise

                self._websession = websession
                self._client = client

            return self._client

    async def get_location(self, location_name: str | None = None) -> Location:
        if not location_name:
            location_name = self._settings.evohome_location_name

        client = await self._get_client()
        for location in client.locations:
            if location.name == location_name:
                await self._update_location(location)
                await asyncio.gather(
                    *[
                        self._fetch_schedules(system)
                        for system in get_control_systems(location)
                        if self._schedules_need_refresh(system)
                    ],
                )
                return location

        raise LocationNotFound(location_name)

    @_retry
    async def set_system_mode(self, control_system: ControlSystem, new_mode: SystemMode) -> None:
        await control_system.set_mode(new_mode)

    @_retry
    async def _update_location(self, location: Location) -> None:
        await location.update()

    @_retry
    async def _fetch_schedules(self, system: ControlSystem) -> None:
        await system.get_schedules()
        self._schedule_refresh_times[system.id] = datetime.now()

    def _schedules_need_refresh(self, system: ControlSystem) -> bool:
        last_refresh = self._schedule_refresh_times.get(system.id)
        return last_refresh is None or datetime.now() - last_refresh >= _SCHEDULE_REFRESH_INTERVAL
