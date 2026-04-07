from aioresponses import aioresponses

from evohome_helper import weather


async def test_get_current_temperature_returns_default_when_entity_missing(monkeypatch):
    monkeypatch.setattr("settings.HOMEASSISTANT_AUTO_ECO_WEATHER_ENTITY", None)

    assert await weather.get_current_temperature() == -99


async def test_get_current_temperature_rounds_value(monkeypatch):
    monkeypatch.setattr("settings.HOMEASSISTANT_AUTO_ECO_WEATHER_ENTITY", "weather.home")

    with aioresponses() as m:
        m.get("http://ha.local/api/states/weather.home", payload={"attributes": {"temperature": 18.64}})
        assert await weather.get_current_temperature() == 18.6


async def test_get_current_temperature_returns_default_on_failure(monkeypatch):
    monkeypatch.setattr("settings.HOMEASSISTANT_AUTO_ECO_WEATHER_ENTITY", "weather.home")

    with aioresponses() as m:
        m.get("http://ha.local/api/states/weather.home", status=500)
        assert await weather.get_current_temperature() == -99
