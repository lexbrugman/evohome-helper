#!/usr/bin/env python

import logging
import os
import time

from logging import config as log_config

log_config.fileConfig(os.path.join(os.path.dirname(__file__), "logging.conf"))
logger = logging.getLogger(__name__)


def set_thermostat_mode():
    location = evohome.get_location()

    zones = evohome.get_zones(location)
    for zone in zones:
        temperature_status = zone.temperatureStatus
        setpoint_status = zone.setpointStatus

        logger.info(
            "%s: %s/%s (%s)",
            zone.name,
            temperature_status["temperature"],
            setpoint_status["targetHeatTemperature"],
            setpoint_status["setpointMode"],
        )

    if presence.is_someone_home():
        logger.info("someone is home")
        evohome.set_normal(location)

    else:
        logger.info("no one is home")

        if presence.is_in_away_grace_period() and evohome.is_in_schedule_grace_period(location):
            logger.info("in grace period of schedule start time")
            evohome.set_normal(location)
        else:
            evohome.set_away(location)


if __name__ == "__main__":
    from evohome_helper import evohome
    from evohome_helper import presence

    while True:
        try:
            set_thermostat_mode()
        except Exception:
            logger.exception("error in loop")

        time.sleep(300)
