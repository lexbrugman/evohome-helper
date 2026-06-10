import aiohttp
import pytest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from evohomeasync2.exceptions import ApiRequestFailedError, BadUserCredentialsError
from tenacity import wait_none

from evohome_helper import evohome_client


async def test_get_location_uses_default_name(monkeypatch, installed_evohome_client):
    monkeypatch.setattr("settings.EVOHOME_LOCATION_NAME", "MyHome")
    state = installed_evohome_client(location_name="MyHome")

    loc = await evohome_client.get_location()

    assert loc is state.location


async def test_get_location_raises_for_missing_location(installed_evohome_client):
    installed_evohome_client(location_name="KnownLocation")

    with pytest.raises(evohome_client.LocationNotFound):
        await evohome_client.get_location("unknown")


async def test_client_returns_existing_client():
    fake_client = object()
    evohome_client._evohome_client = fake_client

    result = await evohome_client._client()

    assert result is fake_client


async def test_client_creates_and_initializes(monkeypatch, tmp_path):
    mock_client = AsyncMock()
    monkeypatch.setattr("evohome_helper.evohome_client.EvohomeClient", lambda *a, **kw: mock_client)
    monkeypatch.setattr("settings.EVOHOME_TOKEN_CACHE_PATH", str(tmp_path / "tokens.json"))

    result = await evohome_client._client()

    mock_client.update.assert_awaited_once_with(dont_update_status=True)
    assert result is mock_client


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


async def test_token_manager_handles_missing_cache(tmp_path):
    async with aiohttp.ClientSession() as session:
        manager = evohome_client._TokenManager("user", "pass", session, str(tmp_path / "missing.json"))
        await manager.load_access_token()

    assert manager.access_token == ""
    assert not manager.is_token_valid()


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
            raise ApiRequestFailedError("temporary failure")
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


async def test_get_location_skips_recently_fetched_schedules(installed_evohome_client):
    state = installed_evohome_client()

    await evohome_client.get_location()
    await evohome_client.get_location()

    state.control_system.get_schedules.assert_awaited_once()


async def test_get_location_refetches_schedules_after_refresh_interval(installed_evohome_client):
    state = installed_evohome_client()

    await evohome_client.get_location()
    evohome_client._schedule_refresh_times[state.control_system.id] -= evohome_client._SCHEDULE_REFRESH_INTERVAL
    await evohome_client.get_location()

    assert state.control_system.get_schedules.await_count == 2


async def test_close_invalidates_schedule_cache(installed_evohome_client):
    installed_evohome_client()
    await evohome_client.get_location()

    await evohome_client.close()

    assert evohome_client._schedule_refresh_times == {}
