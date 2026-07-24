from dataclasses import replace

from evohome_helper.weather import WeatherService


async def test_get_current_temperature_returns_none_when_entity_missing(fake_homeassistant, settings):
    weather = WeatherService(fake_homeassistant, replace(settings, homeassistant_auto_eco_weather_entity=None))

    assert await weather.get_current_temperature() is None


async def test_get_current_temperature_rounds_value(fake_homeassistant, settings):
    fake_homeassistant.set_state("weather.home", {"attributes": {"temperature": 18.64}})
    weather = WeatherService(fake_homeassistant, settings)

    assert await weather.get_current_temperature() == 18.6


async def test_get_current_temperature_returns_none_on_failure(fake_homeassistant, settings):
    # entity state unavailable (get_entity_state returns None)
    weather = WeatherService(fake_homeassistant, settings)

    assert await weather.get_current_temperature() is None


async def test_get_current_temperature_returns_none_when_attribute_missing(fake_homeassistant, settings):
    fake_homeassistant.set_state("weather.home", {"attributes": {}})
    weather = WeatherService(fake_homeassistant, settings)

    assert await weather.get_current_temperature() is None


async def test_get_current_temperature_coerces_numeric_strings(fake_homeassistant, settings):
    # template weather entities may expose the temperature as a string
    fake_homeassistant.set_state("weather.home", {"attributes": {"temperature": "18.64"}})
    weather = WeatherService(fake_homeassistant, settings)

    assert await weather.get_current_temperature() == 18.6


async def test_get_current_temperature_returns_none_for_non_numeric_values(fake_homeassistant, settings):
    # a bad reading must never abort the control cycle
    fake_homeassistant.set_state("weather.home", {"attributes": {"temperature": "unknown"}})
    weather = WeatherService(fake_homeassistant, settings)

    assert await weather.get_current_temperature() is None
