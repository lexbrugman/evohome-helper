import logging

from datetime import datetime, timedelta
from typing import Generator

from evohomeasync2 import ControlSystem, DayOfWeek, Location, SystemMode, Zone, ZoneMode
from evohomeasync2.exceptions import InvalidScheduleError

from evohome_helper.evohome_client import EvohomeService, get_control_systems
from evohome_helper.weather import WeatherService
from settings import Settings

logger = logging.getLogger(__name__)

_AWAY_MODE_MAP = {
    "auto": SystemMode.AUTO,
    "off": SystemMode.HEATING_OFF,
    "eco": SystemMode.AUTO_WITH_ECO,
    "away": SystemMode.AWAY,
    "day_off": SystemMode.DAY_OFF,
    "custom": SystemMode.CUSTOM,
}

# DayOfWeek is the single source of truth for weekday order; it is Monday-first, matching datetime.weekday()
_WEEKDAY_INDEX = {day.value: index for index, day in enumerate(DayOfWeek)}


def get_current_time(location: Location) -> datetime:
    return location.now().replace(microsecond=0)


def _switchpoint_to_datetime(day_of_week: str, time_of_day: str, now: datetime) -> datetime:
    target_weekday = _WEEKDAY_INDEX[str(day_of_week).lower()]
    days_ago = (now.weekday() - target_weekday) % 7
    hour, minute, second = (int(x) for x in time_of_day.split(":"))
    switchpoint_date = now.date() - timedelta(days=days_ago)
    switchpoint_datetime = datetime(switchpoint_date.year, switchpoint_date.month, switchpoint_date.day, hour, minute, second, tzinfo=now.tzinfo)
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
    return max(switch_points, key=lambda point: point[0])[1]


class EvohomeController:
    def __init__(self, evohome_service: EvohomeService, weather: WeatherService, settings: Settings):
        self._evohome = evohome_service
        self._weather = weather
        self._settings = settings

    def validate_configuration(self) -> None:
        # fail fast at startup instead of raising a KeyError deep inside the loop
        if self._settings.evohome_away_mode not in _AWAY_MODE_MAP:
            raise ValueError(f"invalid away_mode '{self._settings.evohome_away_mode}'; must be one of {sorted(_AWAY_MODE_MAP)}")

    def get_zones(self, location: Location) -> Generator[Zone, None, None]:
        for control_system in get_control_systems(location):
            for zone in control_system.zones:
                if zone.active_faults:
                    continue

                yield zone

    def is_in_schedule_grace_period(self, location: Location) -> bool:
        now = get_current_time(location)

        for zone in self.get_zones(location):
            switch_point = self._get_last_heating_switchpoint(zone, now)
            if switch_point is None:
                logger.debug("no scheduled heating switch point found for %s", zone.name)
                continue

            switch_point_start, switch_point_temperature = switch_point
            logger.debug(
                "last scheduled switch point for %s was at: %s (%s degrees celsius)",
                zone.name,
                switch_point_start,
                switch_point_temperature,
            )

            since_switch_point = now - switch_point_start
            if since_switch_point.total_seconds() < self._settings.presence_heating_schedule_grace_time:
                return True

        return False

    async def set_normal(self, location: Location) -> None:
        if await self._is_normal_heating_needed(location):
            await self._set_mode(SystemMode.AUTO, location)
        else:
            await self._set_mode(SystemMode.AUTO_WITH_ECO, location)

    async def set_away(self, location: Location) -> None:
        await self._set_mode(self._get_desired_away_mode(), location)

    def _get_last_heating_switchpoint(self, zone: Zone, now: datetime) -> tuple[datetime, float] | None:
        last_heating_datetime = None
        last_heating_temperature = None
        for switchpoint_datetime, switchpoint_temperature in _get_zone_switch_points(zone, now):
            if not self._is_considered_off(switchpoint_temperature):
                last_heating_datetime = switchpoint_datetime
                last_heating_temperature = switchpoint_temperature

        if last_heating_datetime is None or last_heating_temperature is None:
            return None

        return last_heating_datetime, last_heating_temperature

    def _is_considered_off(self, temperature: float) -> bool:
        return temperature <= self._settings.evohome_off_temp_threshold

    def _get_desired_away_mode(self) -> SystemMode:
        return _AWAY_MODE_MAP[self._settings.evohome_away_mode]

    def _get_override_modes(self) -> set:
        excluded = {SystemMode.AUTO, SystemMode.AUTO_WITH_ECO, self._get_desired_away_mode()}
        return set(SystemMode) - excluded

    def _is_override_enabled(self, control_system: ControlSystem) -> bool:
        if control_system.mode in self._get_override_modes():
            return True

        return any(zone.mode != ZoneMode.FOLLOW_SCHEDULE for zone in control_system.zones)

    async def _is_normal_heating_needed(self, location: Location) -> bool:
        if not self._settings.auto_eco_enabled:
            return True

        highest_set_point_temp = self._get_highest_set_point_temp(location)

        # no valid active setpoint, or all zones are off?
        if highest_set_point_temp is None or self._is_considered_off(highest_set_point_temp):
            return True

        # can we fetch a valid temperature?
        outside_current_temp = await self._weather.get_current_temperature()
        if outside_current_temp is None:
            return True

        logger.debug("current outside temperature: %s degrees celsius", outside_current_temp)

        # are we below the eco mode threshold?
        if outside_current_temp < self._settings.auto_eco_outside_temp_threshold:
            return True

        return outside_current_temp + self._settings.auto_eco_inside_temp_diff < highest_set_point_temp

    def _get_highest_set_point_temp(self, location: Location) -> float | None:
        zones = list(self.get_zones(location))
        if not zones:
            return None
        now = get_current_time(location)
        active_setpoints = (_get_active_setpoint(zone, now) for zone in zones)
        valid_setpoints = filter(lambda setpoint: setpoint is not None, active_setpoints)
        return max(valid_setpoints, default=None)

    async def _set_mode(self, new_mode: SystemMode, location: Location) -> None:
        for control_system in get_control_systems(location):
            if new_mode == control_system.mode:
                continue

            if self._is_override_enabled(control_system):
                logger.warning("not changing thermostat (%s) mode, override is set", control_system.id)
                continue

            logger.debug("changing thermostat (%s) mode to '%s'", control_system.id, new_mode)
            await self._evohome.set_system_mode(control_system, new_mode)
