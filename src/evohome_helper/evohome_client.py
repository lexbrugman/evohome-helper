import aiohttp
import asyncio
import json
import logging
import settings

from datetime import datetime, timedelta
from evohomeasync2 import EvohomeClient, ControlSystem, Location
from evohomeasync2.auth import AbstractTokenManager
from evohomeasync2.exceptions import ApiRequestFailedError, AuthenticationFailedError, BadUserCredentialsError
from evohomeasync2.schemas import SystemMode
from tenacity import retry, retry_if_exception_type, retry_if_not_exception_type, stop_after_attempt, wait_exponential
from typing import Generator

logger = logging.getLogger(__name__)
_evohome_client: EvohomeClient | None = None
_websession: aiohttp.ClientSession | None = None

# only retry errors that can resolve on their own; invalid credentials never will
_TRANSIENT_ERRORS = (
    aiohttp.ClientError,
    TimeoutError,
    ApiRequestFailedError,
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
_schedule_refresh_times: dict[str, datetime] = {}


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
        with open(self._token_cache_path, "w") as f:
            json.dump(self._export_access_token(), f)


async def close() -> None:
    global _evohome_client, _websession

    _schedule_refresh_times.clear()
    _evohome_client = None

    if _websession is not None:
        await _websession.close()
        _websession = None


# discard the cached client so the next call recreates it from scratch
reset_client = close


@_retry
async def _client() -> EvohomeClient:
    global _evohome_client, _websession

    if _evohome_client is None:
        websession = aiohttp.ClientSession()
        try:
            token_manager = _TokenManager(
                settings.EVOHOME_USERNAME,
                settings.EVOHOME_PASSWORD,
                websession,
                settings.EVOHOME_TOKEN_CACHE_PATH,
            )
            await token_manager.load_access_token()

            new_client = EvohomeClient(token_manager)
            await new_client.update(dont_update_status=True)
        except BaseException:
            await websession.close()
            raise

        _websession = websession
        _evohome_client = new_client

    return _evohome_client


def get_control_systems(location: Location) -> Generator[ControlSystem, None, None]:
    for gateway in location.gateways:
        for control_system in gateway.systems:
            yield control_system


async def get_location(location_name: str | None = None) -> Location:
    if not location_name:
        location_name = settings.EVOHOME_LOCATION_NAME

    client = await _client()
    for location in client.locations:
        if location.name == location_name:
            await _update_location(location)
            await asyncio.gather(
                *[
                    _fetch_schedules(system)
                    for system in get_control_systems(location)
                    if _schedules_need_refresh(system)
                ],
            )
            return location

    raise LocationNotFound(location_name)


@_retry
async def set_system_mode(control_system: ControlSystem, new_mode: SystemMode) -> None:
    await control_system.set_mode(new_mode)


@_retry
async def _update_location(location: Location) -> None:
    await location.update()


@_retry
async def _fetch_schedules(system: ControlSystem) -> None:
    await system.get_schedules()
    _schedule_refresh_times[system.id] = datetime.now()


def _schedules_need_refresh(system: ControlSystem) -> bool:
    last_refresh = _schedule_refresh_times.get(system.id)
    return last_refresh is None or datetime.now() - last_refresh >= _SCHEDULE_REFRESH_INTERVAL
