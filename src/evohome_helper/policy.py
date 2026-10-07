"""The heating policy: what mode the thermostat should be in, given the situation.

Pure: no I/O and no library objects, so the whole policy can be read top to bottom and
tested as a table. The loop in main gathers the situation and applies the decision."""

from dataclasses import dataclass

from evohomeasync2 import SystemMode

from settings import Settings

_AWAY_MODE_MAP = {
    "auto": SystemMode.AUTO,
    "off": SystemMode.HEATING_OFF,
    "eco": SystemMode.AUTO_WITH_ECO,
    "away": SystemMode.AWAY,
    "day_off": SystemMode.DAY_OFF,
    "custom": SystemMode.CUSTOM,
}


@dataclass(frozen=True)
class Situation:
    someone_home: bool | None  # None: presence could not be determined
    someone_home_recently: bool
    in_preheat_window: bool
    highest_scheduled_setpoint: float | None  # None: no zone has a usable schedule
    outside_temperature: float | None  # None: unknown, or auto-eco disabled


@dataclass(frozen=True)
class Decision:
    mode: SystemMode | None  # None: leave the thermostat unchanged
    reason: str


def validate_configuration(settings: Settings) -> None:
    # fail fast at startup instead of raising a KeyError deep inside the loop
    if settings.evohome_away_mode not in _AWAY_MODE_MAP:
        raise ValueError(f"invalid away_mode '{settings.evohome_away_mode}'; must be one of {sorted(_AWAY_MODE_MAP)}")


def get_away_mode(settings: Settings) -> SystemMode:
    return _AWAY_MODE_MAP[settings.evohome_away_mode]


def decide(situation: Situation, settings: Settings) -> Decision:
    if situation.someone_home is None:
        # fabricating an away reading would let a Home Assistant outage turn the heating down
        return Decision(None, "presence could not be determined; leaving the thermostat unchanged")

    if situation.someone_home:
        return _normal(situation, settings, "someone is home")

    if situation.someone_home_recently and situation.in_preheat_window:
        return _normal(situation, settings, "no one is home, but pre-heating after a scheduled setpoint increase as the home is in daily use")

    return Decision(get_away_mode(settings), "no one is home")


def _normal(situation: Situation, settings: Settings, reason: str) -> Decision:
    """Normal heating is AUTO, or AUTO_WITH_ECO when it is warm enough outside that the
    zones barely need heating to reach their scheduled setpoints."""
    if not settings.auto_eco_enabled:
        return Decision(SystemMode.AUTO, reason)

    highest = situation.highest_scheduled_setpoint
    outside = situation.outside_temperature

    if highest is None:
        return Decision(SystemMode.AUTO, f"{reason}; no scheduled setpoint to compare the outside temperature with")

    if outside is None:
        return Decision(SystemMode.AUTO, f"{reason}; the outside temperature is unknown")

    if outside < settings.auto_eco_outside_temp_threshold:
        return Decision(SystemMode.AUTO, f"{reason}; {outside} degrees outside is below the eco threshold")

    if outside + settings.auto_eco_inside_temp_diff < highest:
        return Decision(SystemMode.AUTO, f"{reason}; {outside} degrees outside is well below the {highest} degrees setpoint")

    return Decision(SystemMode.AUTO_WITH_ECO, f"{reason}; {outside} degrees outside is warm for the {highest} degrees setpoint, using eco")
