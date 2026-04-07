#!/usr/bin/env python

import asyncio
import logging
import os
import settings

from logging import config as log_config

from evohome_helper import evohome
from evohome_helper import presence

log_config.fileConfig(os.path.join(os.path.dirname(__file__), "logging.conf"))
logger = logging.getLogger(__name__)


async def determine_and_set_thermostat_mode() -> None:
    location = await evohome.get_location()

    zones = evohome.get_zones(location)
    for zone in zones:
        temperature_status = zone.temperature_status
        setpoint_status = zone.setpoint_status

        logger.info(
            "%s: %s/%s (%s)",
            zone.name,
            temperature_status["temperature"],
            setpoint_status["target_heat_temperature"],
            setpoint_status["setpoint_mode"],
        )

    if await presence.is_someone_home():
        logger.info("someone is home")
        await evohome.set_normal(location)

    else:
        logger.info("no one is home")

        if await presence.is_in_away_grace_period() and evohome.is_in_schedule_grace_period(location):
            logger.info("in grace period of schedule start time")
            await evohome.set_normal(location)
        else:
            await evohome.set_away(location)


async def main() -> None:
    while True:
        try:
            await determine_and_set_thermostat_mode()
        except Exception:
            logger.exception("error in loop")

        await asyncio.sleep(settings.INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
