import json

import pytest

import settings as settings_module

from settings import AwayMode, Settings

_OPTIONS = {
    "evohome": {"location_name": "MyHome", "username": "u", "password": "p", "away_mode": "eco"},
    "presence": {"entities": [], "last_home_grace_time": 1200, "heating_schedule_grace_time": 1800},
    "auto_eco": {"enabled": False, "weather_entity": "", "outside_temp_threshold": 14.5, "inside_temp_diff": 2.0},
    "interval": 180,
}


def _write_options(tmp_path, options):
    options_path = tmp_path / "options.json"
    options_path.write_text(json.dumps(options))
    return str(options_path)


def test_load_maps_the_options_json_structure(monkeypatch, tmp_path):
    """Pin the mapping between the add-on's options.json and the Settings fields; a key
    rename on either side must fail this test instead of crash-looping the add-on."""
    options = {
        "evohome": {
            "location_name": "MyHome",
            "username": "user@example.org",
            "password": "secret",
            "away_mode": "eco",
        },
        "presence": {
            "entities": ["person.a", "person.b"],
            "last_home_grace_time": 1200,
            "heating_schedule_grace_time": 1800,
        },
        "auto_eco": {
            "enabled": True,
            "weather_entity": "weather.home",
            "outside_temp_threshold": 14.5,
            "inside_temp_diff": 2.0,
        },
        "interval": 180,
    }
    monkeypatch.setattr(settings_module, "_OPTIONS_PATH", _write_options(tmp_path, options))
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-token")

    settings = Settings.load()

    assert settings.evohome_location_name == "MyHome"
    assert settings.evohome_username == "user@example.org"
    assert settings.evohome_password == "secret"
    assert settings.evohome_away_mode == AwayMode.ECO
    assert settings.evohome_token_cache_path == "/data/evohome_token_cache.json"
    assert settings.homeassistant_url == "http://supervisor/core"
    assert settings.homeassistant_token == "supervisor-token"
    assert settings.homeassistant_presence_entities == ["person.a", "person.b"]
    assert settings.homeassistant_auto_eco_weather_entity == "weather.home"
    assert settings.presence_last_home_grace_time == 1200
    assert settings.presence_heating_schedule_grace_time == 1800
    assert settings.auto_eco_enabled is True
    assert settings.auto_eco_outside_temp_threshold == 14.5
    assert settings.auto_eco_inside_temp_diff == 2.0
    assert settings.interval == 180


def _load(monkeypatch, tmp_path, **sections) -> Settings:
    # each given section replaces the default one
    monkeypatch.setattr(settings_module, "_OPTIONS_PATH", _write_options(tmp_path, {**_OPTIONS, **sections}))
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-token")
    return Settings.load()


@pytest.mark.parametrize("weather_entity", [{"weather_entity": ""}, {}], ids=["empty", "absent"])
def test_load_treats_an_empty_or_absent_weather_entity_as_not_configured(monkeypatch, tmp_path, weather_entity):
    # the add-on marks the option as optional (str?), so the key may be missing entirely
    auto_eco = {"enabled": False, "outside_temp_threshold": 14.5, "inside_temp_diff": 2.0, **weather_entity}

    assert _load(monkeypatch, tmp_path, auto_eco=auto_eco).homeassistant_auto_eco_weather_entity is None


@pytest.mark.parametrize(
    "name,mode",
    [("away", AwayMode.AWAY), ("eco", AwayMode.ECO), ("custom", AwayMode.CUSTOM), ("off", AwayMode.OFF)],
)
def test_load_parses_the_away_mode_by_its_option_name(monkeypatch, tmp_path, name, mode):
    evohome = {**_OPTIONS["evohome"], "away_mode": name}

    assert _load(monkeypatch, tmp_path, evohome=evohome).evohome_away_mode == mode


def test_load_rejects_an_unknown_away_mode(monkeypatch, tmp_path):
    evohome = {**_OPTIONS["evohome"], "away_mode": "day_off"}

    with pytest.raises(ValueError, match="day_off"):
        _load(monkeypatch, tmp_path, evohome=evohome)


@pytest.mark.parametrize("interval", [0, -5])
def test_non_positive_interval_is_rejected(interval):
    from conftest import make_settings

    with pytest.raises(ValueError):
        make_settings(interval=interval)
