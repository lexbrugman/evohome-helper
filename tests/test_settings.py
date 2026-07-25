import json

import settings as settings_module

from settings import Settings


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
            "off_temp_threshold": 5.0,
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
        "interval": 300,
    }
    monkeypatch.setattr(settings_module, "_OPTIONS_PATH", _write_options(tmp_path, options))
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-token")

    settings = Settings.load()

    assert settings.evohome_location_name == "MyHome"
    assert settings.evohome_username == "user@example.org"
    assert settings.evohome_password == "secret"
    assert settings.evohome_off_temp_threshold == 5.0
    assert settings.evohome_away_mode == "eco"
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
    assert settings.interval == 300
