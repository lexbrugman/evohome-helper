#!/usr/bin/env python

import asyncio
import logging
import os
import signal
import settings

from logging import config as log_config

from evohome_helper import evohome
from evohome_helper import evohome_client
from evohome_helper import homeassistant
from evohome_helper import presence

log_config.fileConfig(os.path.join(os.path.dirname(__file__), "logging.conf"))
logger = logging.getLogger(__name__)

CONSECUTIVE_FAILURES_BEFORE_RESET = 3


async def determine_and_set_thermostat_mode() -> None:
    location = await evohome_client.get_location()

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
    shutdown_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(shutdown_signal, shutdown_event.set)

    consecutive_failures = 0

    try:
        while not shutdown_event.is_set():
            try:
                await determine_and_set_thermostat_mode()
                consecutive_failures = 0
            except Exception:
                consecutive_failures += 1
                logger.exception("error in loop")

                if consecutive_failures >= CONSECUTIVE_FAILURES_BEFORE_RESET:
                    logger.warning("resetting the evohome client after %d consecutive failures", consecutive_failures)
                    await evohome_client.reset_client()
                    consecutive_failures = 0

            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=settings.INTERVAL)
            except TimeoutError:
                pass
    finally:
        logger.info("shutting down")

        for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(shutdown_signal)

        await homeassistant.close()
        await evohome_client.close()


if __name__ == "__main__":
    asyncio.run(main())
