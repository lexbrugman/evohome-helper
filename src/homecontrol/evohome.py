import inspect
import logging

from datetime import datetime
from evohomeclient2 import EvohomeClient as BaseEvohomeClient
from time import sleep

from homecontrol import settings
from homecontrol import weather

logger = logging.getLogger(__name__)


class ThermostatStatuses:
    auto = {"mode": "Auto", "status": 0}
    off = {"mode": "HeatingOff", "status": 1}
    eco = {"mode": "AutoWithEco", "status": 2}
    away = {"mode": "Away", "status": 3}
    day_off = {"mode": "DayOff", "status": 4}
    custom = {"mode": "Custom", "status": 6}

    @classmethod
    def get_all(cls):
        statuses = inspect.getmembers(cls, lambda a: not inspect.isroutine(a))
        statuses = filter(lambda a: not a[0].startswith("__"), statuses)

        return list(statuses)

    @classmethod
    def get_by_mode(cls, mode):
        statuses = cls.get_all()
        for status_name, status_data in statuses:
            if status_data.get("mode") == mode:
                return getattr(cls, status_name)

        return None


class EvohomeClient(BaseEvohomeClient):
    def get_location(self, name):
        for location in self.locations:
            if location.name == name:
                return location

        return None


def _client(_retries=0):
    try:
        return EvohomeClient(
            settings.EVOHOME_USERNAME,
            settings.EVOHOME_PASSWORD,
        )

    except KeyError:
        if _retries >= 4:
            raise

        sleep(2)

        return _client(_retries + 1)


def get_current_time():
    return datetime.now().replace(microsecond=0)


def get_location():
    return _client().get_location(settings.EVOHOME_LOCATION_NAME)


def is_in_schedule_grace_period(location=None):
    now = get_current_time()

    zones = get_zones(location)
    for zone in zones:
        switch_point, switch_point_temperature = _get_current_zone_switch_point_from_schedule(zone)
        logger.debug(
            "last scheduled switch point for %s was at: %s (%s degrees celsius)",
            zone.name,
            switch_point,
            switch_point_temperature
        )

        since_switch_point = now - switch_point
        if since_switch_point.total_seconds() < settings.EVOHOME_SCHEDULE_GRACE_TIME:
            return True

    return False


def _parse_switch_point(switch_point):
    now = get_current_time()
    cur_year = int(now.strftime("%Y"))
    cur_week_num = now.strftime("%U")

    # change from 0 = monday to 0 = sunday
    switch_point_week_day_num = switch_point["DayOfWeek"] + 1
    if switch_point_week_day_num == 7:
        switch_point_week_day_num = 0

    switch_point_time_string = "{}-{}-{} {}".format(
        cur_year,
        cur_week_num,
        switch_point_week_day_num,
        switch_point["TimeOfDay"]
    )

    return datetime.strptime(
        switch_point_time_string,
        "%Y-%U-%w %H:%M:%S"
    )


def _get_current_zone_switch_point_from_schedule(zone):
    last_switch_point = _parse_switch_point({
        "DayOfWeek": 6,
        "TimeOfDay": "00:00:00",
    })
    last_switch_point_temperature = -99

    now = get_current_time()

    zone_schedule_days = zone.schedule()["DailySchedules"]
    for zone_schedule_day in zone_schedule_days:
        zone_switch_points = zone_schedule_day["Switchpoints"]
        for zone_switch_point in zone_switch_points:
            zone_switch_point = dict(zone_switch_point)
            zone_switch_point["DayOfWeek"] = zone_schedule_day["DayOfWeek"]

            if zone_switch_point["TargetTemperature"] <= settings.EVOHOME_SCHEDULE_OFF_TEMP:
                continue

            switch_point = _parse_switch_point(zone_switch_point)
            if switch_point > now or switch_point < last_switch_point:
                continue

            last_switch_point = switch_point
            last_switch_point_temperature = zone_switch_point["TargetTemperature"]

    return last_switch_point, last_switch_point_temperature


def get_zones(location=None):
    if not location:
        location = get_location()

    for gateway in location.gateways.values():
        for control_system in gateway.control_systems.values():
            for zone in control_system.zones.values():
                if zone.activeFaults:
                    continue

                yield zone


def _is_override_enabled(control_system):
    current_mode = ThermostatStatuses.get_by_mode(control_system.systemModeStatus["mode"])
    if current_mode in [ThermostatStatuses.away, ThermostatStatuses.day_off, ThermostatStatuses.off]:
        return True

    for zone in control_system.zones.values():
        setpoint_status = zone.heatSetpointStatus
        if setpoint_status["setpointMode"] != "FollowSchedule":
            return True

    return False


def _is_heating_needed(location=None):
    if not location:
        location = get_location()

    location_string = "{}, {}".format(location.city, location.country)
    outside_high_temperature, outside_current_temperature = weather.get_temperature_info(location_string)

    logger.debug(
        "current outside (%s) temperature: %s degrees celsius (today high = %s)",
        location_string,
        outside_current_temperature,
        outside_high_temperature,
    )

    # is it a warm day?
    if outside_high_temperature < settings.EVOHOME_HEATING_ECO_TEMPERATURE:
        return True

    highest_set_point_temp = _get_highest_set_point_temp(location)

    # all zones are off?
    if highest_set_point_temp <= settings.EVOHOME_SCHEDULE_OFF_TEMP:
        return True

    temperature_offset = float(settings.EVOHOME_HEATING_ECO_TEMPERATURE_OFFSET)

    return outside_current_temperature + temperature_offset < highest_set_point_temp


def _get_highest_set_point_temp(location=None):
    highest_set_point_temperature = -99

    zones = get_zones(location)
    for zone in zones:
        switch_point, set_point_temperature = _get_current_zone_switch_point_from_schedule(zone)

        if set_point_temperature > highest_set_point_temperature:
            highest_set_point_temperature = set_point_temperature

    return highest_set_point_temperature


def _set_mode(new_mode, location=None):
    if not location:
        location = get_location()

    for gateway in location.gateways.values():
        for control_system in gateway.control_systems.values():
            current_mode = ThermostatStatuses.get_by_mode(control_system.systemModeStatus["mode"])
            if new_mode == current_mode:
                continue

            if _is_override_enabled(control_system):
                logger.warning("not changing thermostat (%s) mode, override is set", control_system.systemId)
                continue

            logger.debug("changing thermostat (%s) mode to '%s'", control_system.systemId, new_mode["mode"])
            control_system._set_status(new_mode["status"])


def set_normal(location=None):
    if _is_heating_needed(location):
        _set_mode(ThermostatStatuses.auto, location)
    else:
        _set_mode(ThermostatStatuses.eco, location)


def set_away(location=None):
    _set_mode(ThermostatStatuses.custom, location)
