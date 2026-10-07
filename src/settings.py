import json
import os

from dataclasses import dataclass

# where the Home Assistant supervisor mounts the add-on configuration
_OPTIONS_PATH = "/data/options.json"


@dataclass(frozen=True)
class Settings:
    evohome_location_name: str
    evohome_username: str
    evohome_password: str
    evohome_away_mode: str
    evohome_token_cache_path: str
    homeassistant_url: str
    homeassistant_token: str
    homeassistant_presence_entities: list[str]
    homeassistant_auto_eco_weather_entity: str | None
    presence_last_home_grace_time: float
    presence_heating_schedule_grace_time: float
    auto_eco_enabled: bool
    auto_eco_outside_temp_threshold: float
    auto_eco_inside_temp_diff: float
    interval: float

    def __post_init__(self) -> None:
        # fail fast at startup: a non-positive interval would hammer the vendor API in a tight loop
        if self.interval <= 0:
            raise ValueError(f"invalid interval {self.interval!r}; must be positive")

    @classmethod
    def load(cls) -> "Settings":
        # read the add-on configuration from Home Assistant; called once at startup so
        # that importing this module has no side effects (and works outside the add-on)
        with open(_OPTIONS_PATH) as f:
            config = json.load(f)

        return cls(
            evohome_location_name=config["evohome"]["location_name"],
            evohome_username=config["evohome"]["username"],
            evohome_password=config["evohome"]["password"],
            evohome_away_mode=config["evohome"]["away_mode"],
            evohome_token_cache_path="/data/evohome_token_cache.json",
            homeassistant_url="http://supervisor/core",
            homeassistant_token=os.environ["SUPERVISOR_TOKEN"],
            homeassistant_presence_entities=config["presence"]["entities"],
            # the option is optional: an empty or absent entity id means "not configured"
            homeassistant_auto_eco_weather_entity=config["auto_eco"].get("weather_entity") or None,
            presence_last_home_grace_time=config["presence"]["last_home_grace_time"],
            presence_heating_schedule_grace_time=config["presence"]["heating_schedule_grace_time"],
            auto_eco_enabled=config["auto_eco"]["enabled"],
            auto_eco_outside_temp_threshold=config["auto_eco"]["outside_temp_threshold"],
            auto_eco_inside_temp_diff=config["auto_eco"]["inside_temp_diff"],
            interval=config["interval"],
        )
