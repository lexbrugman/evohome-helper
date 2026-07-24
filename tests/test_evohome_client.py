import json
import os
import stat

import aiohttp
import pytest

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from evohomeasync2 import SystemMode
from evohomeasync2.exceptions import ApiCallFailedError, AuthenticationFailedError, BadUserCredentialsError, InvalidSystemModeError
from tenacity import wait_none

from evohome_helper import evohome_client
from evohome_helper.evohome_client import EvohomeService


async def test_get_location_uses_default_name(make_service, settings, evohome_factory):
    state = evohome_factory.complete_state(location_name="MyHome")
    service = make_service(config=replace(settings, evohome_location_name="MyHome"), locations=[state.location])

    loc = await service.get_location()

    assert loc is state.location


async def test_get_location_raises_for_missing_location(make_service, evohome_factory):
    state = evohome_factory.complete_state(location_name="KnownLocation")
    service = make_service(locations=[state.location])

    with pytest.raises(evohome_client.LocationNotFound):
        await service.get_location("unknown")


async def test_client_returns_existing_client(make_service):
    fake_client = object()
    service = make_service(client=fake_client)

    result = await service._get_client()

    assert result is fake_client


async def test_client_creates_and_initializes(monkeypatch, settings, tmp_path):
    mock_client = AsyncMock()
    monkeypatch.setattr("evohome_helper.evohome_client.EvohomeClient", lambda *a, **kw: mock_client)
    service = EvohomeService(replace(settings, evohome_token_cache_path=str(tmp_path / "tokens.json")))

    result = await service._get_client()

    mock_client.update.assert_awaited_once_with(dont_update_status=True)
    assert result is mock_client
    await service.close()


async def test_get_client_cleans_up_session_and_stays_unset_on_failure(monkeypatch, settings, tmp_path):
    created = []
    real_ctor = aiohttp.ClientSession

    def _spy_ctor(*a, **kw):
        session = real_ctor(*a, **kw)
        created.append(session)
        return session

    monkeypatch.setattr("aiohttp.ClientSession", _spy_ctor)

    mock_client = AsyncMock()
    # a non-transient error fails fast (no retries), and _get_client must clean up the session
    mock_client.update = AsyncMock(side_effect=BadUserCredentialsError("bad credentials", status=400))
    monkeypatch.setattr("evohome_helper.evohome_client.EvohomeClient", lambda *a, **kw: mock_client)

    service = EvohomeService(replace(settings, evohome_token_cache_path=str(tmp_path / "tokens.json")))

    with pytest.raises(BadUserCredentialsError):
        await service._get_client()

    assert service._client is None
    assert service._websession is None
    assert created and created[0].closed


async def test_token_manager_round_trips_tokens(tmp_path):
    cache_path = str(tmp_path / "tokens.json")

    async with aiohttp.ClientSession() as session:
        manager = evohome_client._TokenManager("user", "pass", session, cache_path)
        manager._access_token = "access"
        manager._access_token_expires = datetime.now(UTC) + timedelta(minutes=30)
        manager._refresh_token = "refresh"
        await manager.save_access_token()

        restored = evohome_client._TokenManager("user", "pass", session, cache_path)
        await restored.load_access_token()

    assert restored.access_token == "access"
    assert restored.refresh_token == "refresh"
    assert restored.is_token_valid()


async def test_token_cache_is_written_owner_only(tmp_path):
    cache_path = tmp_path / "tokens.json"

    async with aiohttp.ClientSession() as session:
        manager = evohome_client._TokenManager("user", "pass", session, str(cache_path))
        manager._access_token = "access"
        manager._access_token_expires = datetime.now(UTC) + timedelta(minutes=30)
        manager._refresh_token = "refresh"
        await manager.save_access_token()

    assert stat.S_IMODE(os.stat(cache_path).st_mode) == 0o600


async def test_token_manager_handles_missing_cache(tmp_path):
    async with aiohttp.ClientSession() as session:
        manager = evohome_client._TokenManager("user", "pass", session, str(tmp_path / "missing.json"))
        await manager.load_access_token()

    assert manager.access_token == ""
    assert not manager.is_token_valid()


async def test_token_cache_from_another_account_is_ignored(tmp_path):
    cache_path = str(tmp_path / "tokens.json")

    async with aiohttp.ClientSession() as session:
        manager = evohome_client._TokenManager("old-user", "pass", session, cache_path)
        manager._access_token = "access"
        manager._access_token_expires = datetime.now(UTC) + timedelta(minutes=30)
        manager._refresh_token = "refresh"
        await manager.save_access_token()

        # changed credentials must not keep authenticating as the old account
        other = evohome_client._TokenManager("new-user", "pass", session, cache_path)
        await other.load_access_token()

    assert other.access_token == ""
    assert not other.is_token_valid()


async def test_token_cache_without_account_binding_is_ignored(tmp_path):
    # a cache written before the username was stored cannot be attributed to an account
    cache_path = tmp_path / "tokens.json"
    cache_path.write_text(json.dumps({
        "access_token": "access",
        "access_token_expires": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
        "refresh_token": "refresh",
    }))

    async with aiohttp.ClientSession() as session:
        manager = evohome_client._TokenManager("user", "pass", session, str(cache_path))
        await manager.load_access_token()

    assert manager.access_token == ""


async def test_token_manager_ignores_corrupt_cache(tmp_path):
    cache_path = tmp_path / "tokens.json"
    cache_path.write_text("not json")

    async with aiohttp.ClientSession() as session:
        manager = evohome_client._TokenManager("user", "pass", session, str(cache_path))
        await manager.load_access_token()

    assert manager.access_token == ""


async def test_retry_retries_transient_errors():
    calls = {"count": 0}

    @evohome_client._retry
    async def flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise ApiCallFailedError("temporary failure")
        return "ok"

    flaky.retry.wait = wait_none()

    assert await flaky() == "ok"
    assert calls["count"] == 3


async def test_retry_gives_up_immediately_on_bad_credentials():
    calls = {"count": 0}

    @evohome_client._retry
    async def bad_creds():
        calls["count"] += 1
        raise BadUserCredentialsError("bad credentials", status=400)

    bad_creds.retry.wait = wait_none()

    with pytest.raises(BadUserCredentialsError):
        await bad_creds()

    assert calls["count"] == 1


@pytest.mark.parametrize("error", [ApiCallFailedError, AuthenticationFailedError])
async def test_retry_gives_up_immediately_on_rate_limit(error):
    # hammering an already-throttled API only deepens the throttle
    calls = {"count": 0}

    @evohome_client._retry
    async def throttled():
        calls["count"] += 1
        raise error("rate limited", status=429)

    throttled.retry.wait = wait_none()

    with pytest.raises(error):
        await throttled()

    assert calls["count"] == 1


async def test_retry_gives_up_immediately_on_permanent_request_errors():
    # InvalidSystemModeError subclasses ApiCallFailedError but can never succeed on retry
    calls = {"count": 0}

    @evohome_client._retry
    async def unsupported():
        calls["count"] += 1
        raise InvalidSystemModeError("unsupported system_mode")

    unsupported.retry.wait = wait_none()

    with pytest.raises(InvalidSystemModeError):
        await unsupported()

    assert calls["count"] == 1


async def test_set_system_mode_does_not_retry_an_unsupported_mode(make_service, evohome_factory):
    control_system = evohome_factory.control_system(allowed_modes=(SystemMode.AUTO, SystemMode.AWAY))
    service = make_service()

    with pytest.raises(InvalidSystemModeError):
        await service.set_system_mode(control_system, SystemMode.CUSTOM)

    assert control_system.set_mode.await_count == 1


async def test_retry_does_not_retry_unexpected_errors():
    calls = {"count": 0}

    @evohome_client._retry
    async def broken():
        calls["count"] += 1
        raise ValueError("bug")

    broken.retry.wait = wait_none()

    with pytest.raises(ValueError):
        await broken()

    assert calls["count"] == 1


async def test_get_location_skips_recently_fetched_schedules(make_service, evohome_factory):
    state = evohome_factory.complete_state()
    service = make_service(locations=[state.location])

    await service.get_location()
    await service.get_location()

    state.control_system.get_schedules.assert_awaited_once()


async def test_get_location_refetches_schedules_after_refresh_interval(make_service, evohome_factory):
    state = evohome_factory.complete_state()
    service = make_service(locations=[state.location])

    await service.get_location()
    service._schedule_refresh_times[state.control_system.id] -= evohome_client._SCHEDULE_REFRESH_INTERVAL
    await service.get_location()

    assert state.control_system.get_schedules.await_count == 2


async def test_close_invalidates_schedule_cache(make_service, evohome_factory):
    state = evohome_factory.complete_state()
    service = make_service(locations=[state.location])
    await service.get_location()

    await service.close()

    assert service._schedule_refresh_times == {}
