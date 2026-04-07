from unittest.mock import AsyncMock

from aioresponses import aioresponses

from evohome_helper import presence


def test_headers_contains_bearer_token():
    headers = presence._headers()

    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Bearer ")
    assert headers["content-type"] == "application/json"


async def test_get_data_returns_parsed_response():
    with aioresponses() as m:
        m.get("http://ha.local/api/states/person.a", payload={
            "state": "home",
            "attributes": {"seconds_since_last_seen": 12},
        })
        result = await presence._get_data("person.a")

    assert result == {"is_someone_home": True, "seconds_since_last_seen": 12}


async def test_get_data_uses_last_known_state_on_request_failure():
    with aioresponses() as m:
        m.get("http://ha.local/api/states/person.a", payload={
            "state": "home",
            "attributes": {"seconds_since_last_seen": 12},
        })
        await presence._get_data("person.a")

        m.get("http://ha.local/api/states/person.a", status=500)
        result = await presence._get_data("person.a")

    assert result == {"is_someone_home": True, "seconds_since_last_seen": 12}


async def test_get_data_default_when_entity_never_seen():
    with aioresponses() as m:
        m.get("http://ha.local/api/states/person.unknown", status=500)
        result = await presence._get_data("person.unknown")

    assert result == {"is_someone_home": False, "seconds_since_last_seen": 0}


async def test_is_someone_home_and_away_grace_period(monkeypatch):
    fake_data = {
        "person.a": {"is_someone_home": False, "seconds_since_last_seen": 100},
        "person.b": {"is_someone_home": True, "seconds_since_last_seen": 9999},
    }
    monkeypatch.setattr("evohome_helper.presence._get_data", AsyncMock(side_effect=lambda eid: fake_data[eid]))

    assert await presence.is_someone_home() is True
    assert await presence.is_in_away_grace_period() is True


async def test_is_in_away_grace_period_false_when_all_expired(monkeypatch):
    fake_data = {
        "person.a": {"is_someone_home": False, "seconds_since_last_seen": 9999},
        "person.b": {"is_someone_home": False, "seconds_since_last_seen": None},
    }
    monkeypatch.setattr("evohome_helper.presence._get_data", AsyncMock(side_effect=lambda eid: fake_data[eid]))

    assert await presence.is_in_away_grace_period() is False


async def test_is_in_away_grace_period_true_when_last_seen_is_zero(monkeypatch):
    fake_data = {
        "person.a": {"is_someone_home": False, "seconds_since_last_seen": 0},
        "person.b": {"is_someone_home": False, "seconds_since_last_seen": 9999},
    }
    monkeypatch.setattr("evohome_helper.presence._get_data", AsyncMock(side_effect=lambda eid: fake_data[eid]))

    assert await presence.is_in_away_grace_period() is True


async def test_is_in_away_grace_period_none_does_not_count_toward_grace(monkeypatch):
    fake_data = {
        "person.a": {"is_someone_home": False, "seconds_since_last_seen": None},
        "person.b": {"is_someone_home": False, "seconds_since_last_seen": 9999},
    }
    monkeypatch.setattr("evohome_helper.presence._get_data", AsyncMock(side_effect=lambda eid: fake_data[eid]))

    assert await presence.is_in_away_grace_period() is False
