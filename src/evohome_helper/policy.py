"""The heating policy: what mode the thermostat should be in, given the situation.

Pure: no I/O and no library objects, so the whole policy can be read top to bottom and
tested as a table. The loop in main gathers the situation and applies the decision."""

from dataclasses import dataclass
from enum import Enum, auto

from settings import Settings


class HeatingMode(Enum):
    NORMAL = auto()  # follow the schedule
    ECO = auto()  # follow the schedule with the eco setback
    AWAY = auto()  # the configured away mode


@dataclass(frozen=True)
class Situation:
    someone_home: bool | None  # None: presence could not be determined
    seconds_since_last_departure: float | None  # None: no departure is known
    seconds_since_scheduled_increase: float | None  # None: no schedule ever raises a setpoint
    highest_scheduled_setpoint: float | None  # None: no zone has a usable schedule
    outside_temperature: float | None  # None: unknown, or auto-eco disabled


@dataclass(frozen=True)
class Decision:
    mode: HeatingMode | None  # None: leave the thermostat unchanged
    reason: str


def decide(situation: Situation, settings: Settings) -> Decision:
    if situation.someone_home is None:
        # fabricating an away reading would let a Home Assistant outage turn the heating down
        return Decision(None, "presence could not be determined; leaving the thermostat unchanged")

    if situation.someone_home:
        return _normal(situation, settings, "someone is home")

    # a scheduled setpoint increase is where the schedule expects people (waking up, coming
    # home), so the home is kept heating for a while to be warm on arrival -- but only a
    # home in daily use, not one that has been empty for days (holiday)
    in_daily_use = _within(situation.seconds_since_last_departure, settings.presence_last_home_grace_time)
    in_preheat_window = _within(situation.seconds_since_scheduled_increase, settings.presence_heating_schedule_grace_time)
    if in_daily_use and in_preheat_window:
        return _normal(situation, settings, "no one is home, but pre-heating after a scheduled setpoint increase as the home is in daily use")

    if _within(situation.seconds_since_last_departure, settings.presence_away_delay):
        # a tracker that briefly drops out must not cost a switch to away and one back;
        # unchanged rather than normal, so a restart during the delay does not undo an
        # away mode that is already set
        return Decision(None, "no one is home, but someone left only just now; leaving the thermostat unchanged")

    return Decision(HeatingMode.AWAY, "no one is home")


def _within(elapsed: float | None, window: float) -> bool:
    # an event that never happened (or whose time is unknown) is in no window
    return elapsed is not None and elapsed < window


def _normal(situation: Situation, settings: Settings, reason: str) -> Decision:
    """Normal heating follows the schedule, with the eco setback when it is warm enough
    outside that the zones barely need heating to reach their scheduled setpoints."""
    if not settings.auto_eco_enabled:
        return Decision(HeatingMode.NORMAL, reason)

    highest = situation.highest_scheduled_setpoint
    outside = situation.outside_temperature

    if highest is None:
        return Decision(HeatingMode.NORMAL, f"{reason}; no scheduled setpoint to compare the outside temperature with")

    if outside is None:
        return Decision(HeatingMode.NORMAL, f"{reason}; the outside temperature is unknown")

    if outside < settings.auto_eco_outside_temp_threshold:
        return Decision(HeatingMode.NORMAL, f"{reason}; {outside} degrees outside is below the eco threshold")

    if outside + settings.auto_eco_inside_temp_diff < highest:
        return Decision(HeatingMode.NORMAL, f"{reason}; {outside} degrees outside is well below the {highest} degrees setpoint")

    return Decision(HeatingMode.ECO, f"{reason}; {outside} degrees outside is warm for the {highest} degrees setpoint, using eco")
