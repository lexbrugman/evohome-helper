from datetime import datetime
from types import SimpleNamespace

from evohome_helper import weather
from freezegun import freeze_time


def test_get_temperature_returns_default_when_entity_missing(monkeypatch):
    monkeypatch.setattr("settings.HOMEASSISTANT_AUTO_ECO_WEATHER_ENTITY", None)

    with freeze_time(datetime.fromtimestamp(100)):
        assert weather.get_temperature() == -99


def test_get_temperature_rounds_value(monkeypatch):
    monkeypatch.setattr("settings.HOMEASSISTANT_AUTO_ECO_WEATHER_ENTITY", "weather.home")
    response = SimpleNamespace(
        ok=True,
        json=lambda: {"attributes": {"temperature": 18.64}},
    )

    monkeypatch.setattr("evohome_helper.weather.requests.get", lambda *args, **kwargs: response)

    with freeze_time(datetime.fromtimestamp(100)):
        assert weather.get_temperature() == 18.6


def test_get_temperature_returns_default_on_failure(monkeypatch):
    monkeypatch.setattr("settings.HOMEASSISTANT_AUTO_ECO_WEATHER_ENTITY", "weather.home")
    monkeypatch.setattr("evohome_helper.weather.requests.get", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fail")))

    with freeze_time(datetime.fromtimestamp(100)):
        assert weather.get_temperature() == -99


def test_get_temperature_cache_hit_and_refresh(monkeypatch):
    monkeypatch.setattr("settings.HOMEASSISTANT_AUTO_ECO_WEATHER_ENTITY", "weather.home")
    state = {"calls": 0}

    def fake_get(*_args, **_kwargs):
        state["calls"] += 1
        return SimpleNamespace(ok=True, json=lambda: {"attributes": {"temperature": 10 + state["calls"]}})

    monkeypatch.setattr("evohome_helper.weather.requests.get", fake_get)

    with freeze_time(datetime.fromtimestamp(100)):
        first = weather.get_temperature()
        second = weather.get_temperature()

    assert first == 11
    assert second == 11
    assert state["calls"] == 1

    with freeze_time(datetime.fromtimestamp(2000)):
        third = weather.get_temperature()

    assert third == 12
    assert state["calls"] == 2
