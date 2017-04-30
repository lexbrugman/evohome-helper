#!/usr/bin/env python

import logging
import time

from logging import config as log_config

log_config.fileConfig("logging.conf")
logger = logging.getLogger(__name__)


def set_thermostat_mode():
    location = evohome.get_location()

    zones = evohome.get_zones(location)
    for zone in zones:
        temperature_status = zone.temperatureStatus
        setpoint_status = zone.heatSetpointStatus

        if not temperature_status["isAvailable"]:
            continue

        logger.info(
            "%s: %s/%s (%s)",
            zone.name,
            temperature_status["temperature"],
            setpoint_status["targetTemperature"],
            setpoint_status["setpointMode"],
        )

    if evohome.is_in_schedule_grace_period(location):
        logger.info("in grace period of schedule start time")
        evohome.set_normal(location)

    elif unifi.is_someone_home():
        logger.info("someone is home")
        evohome.set_normal(location)

    else:
        logger.info("no one is home")
        evohome.set_away(location)


if __name__ == "__main__":
    from homecontrol import evohome
    from homecontrol import unifi

    while True:
        try:
            set_thermostat_mode()
        except:
            logger.exception("error in loop")

        time.sleep(300)
