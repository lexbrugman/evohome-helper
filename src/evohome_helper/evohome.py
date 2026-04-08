import asyncio
import logging
import settings

from datetime import datetime, timedelta
from evohomeasync2 import EvohomeClientOld as EvohomeClient, ControlSystem, Location, Zone
from evohomeasync2.schemas import SystemMode, ZoneMode
from evohome_helper import weather
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from typing import Generator

logger = logging.getLogger(__name__)
_evohome_client = None
_retry = retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(),
    stop=stop_after_attempt(6),
    reraise=True,
)

_AWAY_MODE_MAP = {
    "auto": SystemMode.AUTO,
    "off": SystemMode.HEATING_OFF,
    "eco": SystemMode.AUTO_WITH_ECO,
    "away": SystemMode.AWAY,
    "day_off": SystemMode.DAY_OFF,
    "custom": SystemMode.CUSTOM,
}

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class LocationNotFound(Exception):
    def __init__(self, location_name: str):
        super().__init__(f"the location '{location_name}' does not exist in the evohome account")


@_retry
async def _client() -> EvohomeClient:
    global _evohome_client

    if _evohome_client is None:
        new_client = EvohomeClient(
            settings.EVOHOME_USERNAME,
            settings.EVOHOME_PASSWORD,
        )
        await new_client.update(dont_update_status=True)
        _evohome_client = new_client

    return _evohome_client


def get_current_time(location: Location) -> datetime:
    return location.now().replace(microsecond=0)


def get_control_systems(location: Location) -> Generator[ControlSystem, None, None]:
    for gateway in location.gateways:
        for control_system in gateway.systems:
            yield control_system


@_retry
async def _update_location(location: Location) -> None:
    await location.update()


@_retry
async def _fetch_schedules(system: ControlSystem) -> None:
    await system.get_schedules()


async def get_location(location_name: str = None) -> Location:
    if not location_name:
        location_name = settings.EVOHOME_LOCATION_NAME

    client = await _client()
    for location in client.locations:
        if location.name == location_name:
            await _update_location(location)
            await asyncio.gather(
                *[_fetch_schedules(system) for system in get_control_systems(location)],
            )
            return location

    raise LocationNotFound(location_name)


def _switchpoint_to_datetime(day_of_week: str, time_of_day: str, now: datetime) -> datetime:
    target_weekday = _DAY_NAMES.index(day_of_week)
    days_ago = (now.weekday() - target_weekday) % 7
    hour, minute, second = (int(x) for x in time_of_day.split(":"))
    switchpoint_date = now.date() - timedelta(days=days_ago)
    switchpoint_datetime = datetime(switchpoint_date.year, switchpoint_date.month, switchpoint_date.day, hour, minute, second, tzinfo=now.tzinfo)
    if switchpoint_datetime > now:
        switchpoint_datetime -= timedelta(weeks=1)
    return switchpoint_datetime


def _get_zone_switch_points(zone: Zone, now: datetime) -> list[tuple[datetime, float]]:
    result = []
    for day_schedule in zone.schedule:
        for switchpoint in day_schedule["switchpoints"]:
            switchpoint_datetime = _switchpoint_to_datetime(day_schedule["day_of_week"], switchpoint["time_of_day"], now)
            result.append((switchpoint_datetime, switchpoint["heat_setpoint"]))
    result.sort(key=lambda x: x[0])
    return result


def _get_last_heating_switchpoint(zone: Zone, now: datetime) -> tuple[datetime, float] | None:
    last_heating_datetime = None
    last_heating_temperature = None
    for switchpoint_datetime, switchpoint_temperature in _get_zone_switch_points(zone, now):
        if not _is_considered_off(switchpoint_temperature):
            last_heating_datetime = switchpoint_datetime
            last_heating_temperature = switchpoint_temperature

    if last_heating_datetime is None or last_heating_temperature is None:
        return None

    return last_heating_datetime, last_heating_temperature


def _get_active_setpoint(zone: Zone, now: datetime) -> float | None:
    switch_points = _get_zone_switch_points(zone, now)
    if not switch_points:
        return None
    return switch_points[-1][1]


def is_in_schedule_grace_period(location: Location) -> bool:
    now = get_current_time(location)

    zones = get_zones(location)
    for zone in zones:
        switch_point = _get_last_heating_switchpoint(zone, now)
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
        if since_switch_point.total_seconds() < settings.PRESENCE_HEATING_SCHEDULE_GRACE_TIME:
            return True

    return False


def get_zones(location: Location) -> Generator[Zone, None, None]:
    for control_system in get_control_systems(location):
        for zone in control_system.zones:
            if zone.active_faults:
                continue

            yield zone


def _is_considered_off(temperature: float) -> bool:
    return temperature <= settings.EVOHOME_OFF_TEMP_THRESHOLD


def _get_desired_away_mode() -> SystemMode:
    return _AWAY_MODE_MAP[settings.EVOHOME_AWAY_MODE]


def _get_override_modes() -> set:
    excluded = {SystemMode.AUTO, SystemMode.AUTO_WITH_ECO, _get_desired_away_mode()}
    return set(SystemMode) - excluded


def _is_override_enabled(control_system: ControlSystem) -> bool:
    current_mode = control_system.mode
    if current_mode in _get_override_modes():
        return True

    return any(zone.mode != ZoneMode.FOLLOW_SCHEDULE for zone in control_system.zones)


async def _is_normal_heating_needed(location: Location) -> bool:
    if not settings.AUTO_ECO_ENABLED:
        return True

    outside_temp_threshold = settings.AUTO_ECO_OUTSIDE_TEMP_THRESHOLD
    inside_temp_diff = settings.AUTO_ECO_INSIDE_TEMP_DIFF
    highest_set_point_temp = _get_highest_set_point_temp(location)

    # no valid active setpoint, or all zones are off?
    if highest_set_point_temp is None or _is_considered_off(highest_set_point_temp):
        return True

    # can we fetch a valid temperature?
    outside_current_temp = await weather.get_current_temperature()
    if outside_current_temp <= -99:
        return True

    logger.debug(
        "current outside temperature: %s degrees celsius",
        outside_current_temp,
    )

    # are we below the eco mode threshold?
    if outside_current_temp < outside_temp_threshold:
        return True

    return outside_current_temp + inside_temp_diff < highest_set_point_temp


def _get_highest_set_point_temp(location: Location) -> float | None:
    zones = list(get_zones(location))
    if not zones:
        return None
    now = get_current_time(location)
    active_setpoints = (_get_active_setpoint(zone, now) for zone in zones)
    valid_setpoints = filter(lambda setpoint: setpoint is not None, active_setpoints)
    return max(valid_setpoints, default=None)


@_retry
async def _set_control_system_mode(control_system: ControlSystem, new_mode: SystemMode) -> None:
    await control_system.set_mode(new_mode)


async def _set_mode(new_mode: SystemMode, location: Location) -> None:
    for control_system in get_control_systems(location):
        current_mode = control_system.mode
        if new_mode == current_mode:
            continue

        if _is_override_enabled(control_system):
            logger.warning("not changing thermostat (%s) mode, override is set", control_system.id)
            continue

        logger.debug("changing thermostat (%s) mode to '%s'", control_system.id, new_mode)
        await _set_control_system_mode(control_system, new_mode)


async def set_normal(location: Location) -> None:
    if await _is_normal_heating_needed(location):
        await _set_mode(SystemMode.AUTO, location)
    else:
        await _set_mode(SystemMode.AUTO_WITH_ECO, location)


async def set_away(location: Location) -> None:
    desired_away_mode = _get_desired_away_mode()
    await _set_mode(desired_away_mode, location)
