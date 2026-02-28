from datetime import datetime
from types import SimpleNamespace

from evohome_helper import presence
from freezegun import freeze_time


def test_headers_contains_bearer_token():
    headers = presence._headers()

    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Bearer ")
    assert headers["content-type"] == "application/json"


def test_get_data_uses_last_known_state_on_request_failure(monkeypatch):
    ok_response = SimpleNamespace(
        ok=True,
        json=lambda: {
            "state": "home",
            "attributes": {"seconds_since_last_seen": 12},
        },
    )

    monkeypatch.setattr("evohome_helper.presence.requests.get", lambda *args, **kwargs: ok_response)

    with freeze_time(datetime.fromtimestamp(100)):
        first = presence._get_data("person.a")
        assert first == {"is_someone_home": True, "seconds_since_last_seen": 12}

    def failing_get(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr("evohome_helper.presence.requests.get", failing_get)

    with freeze_time(datetime.fromtimestamp(100)):
        second = presence._get_data("person.a")

    assert second == first


def test_get_data_default_when_entity_never_seen(monkeypatch):
    monkeypatch.setattr("evohome_helper.presence.requests.get", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    with freeze_time(datetime.fromtimestamp(100)):
        value = presence._get_data("person.unknown")

    assert value == {"is_someone_home": False, "seconds_since_last_seen": 0}


def test_is_someone_home_and_away_grace_period(monkeypatch):
    fake_data = {
        "person.a": {"is_someone_home": False, "seconds_since_last_seen": 100},
        "person.b": {"is_someone_home": True, "seconds_since_last_seen": 9999},
    }

    monkeypatch.setattr("evohome_helper.presence._get_data", lambda entity_id: fake_data[entity_id])

    assert presence.is_someone_home() is True
    assert presence.is_in_away_grace_period() is True


def test_is_in_away_grace_period_false_when_all_expired(monkeypatch):
    fake_data = {
        "person.a": {"is_someone_home": False, "seconds_since_last_seen": 9999},
        "person.b": {"is_someone_home": False, "seconds_since_last_seen": None},
    }

    monkeypatch.setattr("evohome_helper.presence._get_data", lambda entity_id: fake_data[entity_id])

    assert presence.is_in_away_grace_period() is False


def test_is_in_away_grace_period_true_when_last_seen_is_zero(monkeypatch):
    fake_data = {
        "person.a": {"is_someone_home": False, "seconds_since_last_seen": 0},
        "person.b": {"is_someone_home": False, "seconds_since_last_seen": 9999},
    }

    monkeypatch.setattr("evohome_helper.presence._get_data", lambda entity_id: fake_data[entity_id])

    assert presence.is_in_away_grace_period() is True


def test_is_in_away_grace_period_none_does_not_count_toward_grace(monkeypatch):
    fake_data = {
        "person.a": {"is_someone_home": False, "seconds_since_last_seen": None},
        "person.b": {"is_someone_home": False, "seconds_since_last_seen": 9999},
    }

    monkeypatch.setattr("evohome_helper.presence._get_data", lambda entity_id: fake_data[entity_id])

    assert presence.is_in_away_grace_period() is False


def test_get_data_cache_hit_and_refresh(monkeypatch):
    state = {"calls": 0}

    def fake_get(*_args, **_kwargs):
        state["calls"] += 1
        return SimpleNamespace(
            ok=True,
            json=lambda: {
                "state": "home" if state["calls"] == 1 else "not_home",
                "attributes": {"seconds_since_last_seen": state["calls"]},
            },
        )

    monkeypatch.setattr("evohome_helper.presence.requests.get", fake_get)

    with freeze_time(datetime.fromtimestamp(100)):
        first = presence._get_data("person.a")
        second = presence._get_data("person.a")

    assert first == second
    assert state["calls"] == 1

    with freeze_time(datetime.fromtimestamp(1000)):
        third = presence._get_data("person.a")

    assert third == {"is_someone_home": False, "seconds_since_last_seen": 2}
    assert state["calls"] == 2
