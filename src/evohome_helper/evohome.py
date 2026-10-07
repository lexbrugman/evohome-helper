import logging

from collections.abc import Iterator
from datetime import datetime, time, timedelta

from evohomeasync2 import ControlSystem, DayOfWeek, FaultType, Location, SystemMode, Zone, ZoneMode
from evohomeasync2.exceptions import InvalidScheduleError

from evohome_helper.evohome_client import EvohomeService, get_control_systems
from evohome_helper.policy import get_away_mode
from settings import Settings

logger = logging.getLogger(__name__)

# DayOfWeek is the single source of truth for weekday order; it is Monday-first, matching datetime.weekday()
_WEEKDAY_INDEX = {day.value: index for index, day in enumerate(DayOfWeek)}

# faults that make a zone's readings and schedule unusable; benign faults (e.g. a low
# battery) leave the zone heating normally, so it must keep counting toward the
# setpoint and pre-heat window calculations
_DATA_UNUSABLE_FAULTS = {
    FaultType.GWY_X_CL,  # GatewayCommunicationLost
    FaultType.ZON_A_CL,  # TempZoneActuatorCommunicationLost
    FaultType.ZON_S_CL,  # TempZoneSensorCommunicationLost
}


def _zone_data_is_unusable(zone: Zone) -> bool:
    # unknown fault types are assumed benign: the zone's data may well still be fine
    return any(fault["fault_type"] in _DATA_UNUSABLE_FAULTS for fault in zone.active_faults)


def _usable_zones(control_system: ControlSystem) -> Iterator[Zone]:
    return (zone for zone in control_system.zones if not _zone_data_is_unusable(zone))


def get_current_time(location: Location) -> datetime:
    # the location's own local time: switch points are local wall-clock times there
    return location.now().replace(microsecond=0)


def _switchpoint_to_datetime(day_of_week: str, time_of_day: str, now: datetime) -> datetime:
    target_weekday = _WEEKDAY_INDEX[str(day_of_week).lower()]
    days_ago = (now.weekday() - target_weekday) % 7
    switchpoint_date = now.date() - timedelta(days=days_ago)
    switchpoint_datetime = datetime.combine(switchpoint_date, time.fromisoformat(time_of_day), tzinfo=now.tzinfo)
    if switchpoint_datetime > now:
        switchpoint_datetime -= timedelta(weeks=1)
    return switchpoint_datetime


def _get_zone_switch_points(zone: Zone, now: datetime) -> list[tuple[datetime, float]]:
    try:
        schedule = zone.schedule
    except InvalidScheduleError:
        # a zone without a (valid) schedule has no switch points to consider
        return []

    result = []
    for day_schedule in schedule:
        for switchpoint in day_schedule["switchpoints"]:
            switchpoint_datetime = _switchpoint_to_datetime(day_schedule["day_of_week"], switchpoint["time_of_day"], now)
            result.append((switchpoint_datetime, switchpoint["heat_setpoint"]))
    result.sort(key=lambda x: x[0])
    return result


def _get_active_setpoint(zone: Zone, now: datetime) -> float | None:
    switch_points = _get_zone_switch_points(zone, now)
    if not switch_points:
        return None
    # the switch points are sorted ascending by datetime, so the last one is active
    return switch_points[-1][1]


def _get_last_setpoint_increase(zone: Zone, now: datetime) -> tuple[datetime, float] | None:
    # a scheduled setpoint increase is where the schedule expects people (waking up,
    # coming home); decreases (setbacks) are not, whatever the temperatures involved
    switch_points = _get_zone_switch_points(zone, now)
    last_increase = None
    for index, (switchpoint_datetime, temperature) in enumerate(switch_points):
        # the week is cyclic, so the earliest switch point follows the latest one
        _, previous_temperature = switch_points[index - 1]
        if temperature > previous_temperature:
            last_increase = (switchpoint_datetime, temperature)
    return last_increase


class EvohomeController:
    """Evohome-side facts for the policy (zones, schedule) and applying its decision."""

    def __init__(self, evohome_service: EvohomeService, settings: Settings):
        self._evohome = evohome_service
        self._settings = settings

    def get_zones(self, location: Location) -> Iterator[Zone]:
        for control_system in get_control_systems(location):
            yield from _usable_zones(control_system)

    def is_in_preheat_window(self, location: Location) -> bool:
        """Whether any zone's schedule raised its setpoint recently: the house is then kept
        heating for a while even if no one is detected yet, so it is warm on arrival."""
        now = get_current_time(location)

        for zone in self.get_zones(location):
            increase = _get_last_setpoint_increase(zone, now)
            if increase is None:
                logger.debug("no scheduled setpoint increase found for %s", zone.name)
                continue

            increase_start, increase_temperature = increase
            logger.debug(
                "last scheduled setpoint increase for %s was at: %s (%s degrees celsius)",
                zone.name,
                increase_start,
                increase_temperature,
            )

            since_increase = now - increase_start
            if since_increase.total_seconds() < self._settings.presence_heating_schedule_grace_time:
                return True

        return False

    def get_highest_scheduled_setpoint(self, location: Location) -> float | None:
        # the *scheduled* setpoint, not the current target: in away mode the target is the
        # away setpoint, which says nothing about how much heat the zones will want
        now = get_current_time(location)
        active_setpoints = (_get_active_setpoint(zone, now) for zone in self.get_zones(location))
        return max((setpoint for setpoint in active_setpoints if setpoint is not None), default=None)

    async def apply(self, new_mode: SystemMode, location: Location) -> None:
        for control_system in get_control_systems(location):
            if new_mode == control_system.mode:
                continue

            if self._is_override_enabled(control_system):
                logger.warning("not changing thermostat (%s) mode, override is set", control_system.id)
                continue

            logger.info("changing thermostat (%s) mode to '%s'", control_system.id, new_mode)
            await self._evohome.set_system_mode(control_system, new_mode)

    def _get_override_modes(self) -> set[SystemMode]:
        # a mode the user set by hand (anything we never set ourselves) must be left alone
        excluded = {SystemMode.AUTO, SystemMode.AUTO_WITH_ECO, get_away_mode(self._settings)}
        return set(SystemMode) - excluded

    def _is_override_enabled(self, control_system: ControlSystem) -> bool:
        if control_system.mode in self._get_override_modes():
            return True

        # a zone whose data is unusable cannot report a meaningful mode either
        return any(zone.mode != ZoneMode.FOLLOW_SCHEDULE for zone in _usable_zones(control_system))
