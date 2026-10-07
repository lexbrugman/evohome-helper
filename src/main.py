#!/usr/bin/env python

import asyncio
import logging
import os
import signal

from logging import config as log_config

from evohome_helper.evohome import EvohomeController
from evohome_helper.evohome_client import EvohomeService, LocationNotFound, is_transient_error
from evohome_helper.homeassistant import HomeAssistantClient
from evohome_helper.presence import PresenceTracker
from evohome_helper.weather import WeatherService
from settings import Settings

log_config.fileConfig(os.path.join(os.path.dirname(__file__), "logging.conf"))
logger = logging.getLogger(__name__)

CONSECUTIVE_FAILURES_BEFORE_RESET = 3


class Application:
    def __init__(
        self,
        settings: Settings,
        evohome_service: EvohomeService,
        controller: EvohomeController,
        presence: PresenceTracker,
        homeassistant: HomeAssistantClient,
    ):
        self._settings = settings
        self._evohome_service = evohome_service
        self._controller = controller
        self._presence = presence
        self._homeassistant = homeassistant

    async def determine_and_set_thermostat_mode(self) -> None:
        await self._presence.refresh()
        location = await self._evohome_service.get_location()

        for zone in self._controller.get_zones(location):
            temperature_status = zone.temperature_status
            setpoint_status = zone.setpoint_status

            logger.info(
                "%s: %s/%s (%s)",
                zone.name,
                # absent when the sensor is unavailable (dead battery, comms lost); the other
                # two keys are guaranteed by the API schema
                temperature_status.get("temperature"),
                setpoint_status["target_heat_temperature"],
                setpoint_status["setpoint_mode"],
            )

        if self._presence.is_someone_home():
            logger.info("someone is home")
            await self._controller.set_normal(location)

        else:
            logger.info("no one is home")

            if not self._presence.is_presence_known():
                logger.warning("presence could not be determined; leaving the thermostat unchanged")
                return

            if self._presence.was_someone_home_recently() and self._controller.is_in_preheat_window(location):
                logger.info("pre-heating after a scheduled setpoint increase; the home is in daily use")
                await self._controller.set_normal(location)
            else:
                await self._controller.set_away(location)

    async def run(self) -> None:
        shutdown_event = asyncio.Event()

        loop = asyncio.get_running_loop()
        for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(shutdown_signal, shutdown_event.set)

        consecutive_failures = 0

        try:
            while not shutdown_event.is_set():
                try:
                    await self.determine_and_set_thermostat_mode()
                    consecutive_failures = 0
                except LocationNotFound as error:
                    # a misconfigured location name can never recover on its own; exit so
                    # the problem is visible instead of retrying forever
                    logger.critical("%s; exiting", error)
                    raise
                except Exception as error:
                    logger.exception("error in loop")

                    # only failures that recreating the evohome client can plausibly cure (a
                    # wedged session, a token the library failed to refresh) count toward a
                    # reset; permanent errors, rate limiting and bugs would just churn the
                    # heavily rate-limited vendor API with pointless re-authentications
                    if is_transient_error(error):
                        consecutive_failures += 1

                    if consecutive_failures >= CONSECUTIVE_FAILURES_BEFORE_RESET:
                        logger.warning("resetting the evohome client after %d consecutive failures", consecutive_failures)
                        await self._evohome_service.reset()
                        consecutive_failures = 0

                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=self._settings.interval)
                except TimeoutError:
                    pass
        finally:
            logger.info("shutting down")

            for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(shutdown_signal)

            await self._homeassistant.close()
            await self._evohome_service.close()


def build_application(settings: Settings) -> Application:
    homeassistant = HomeAssistantClient(settings)
    presence = PresenceTracker(homeassistant, settings)
    weather = WeatherService(homeassistant, settings)
    evohome_service = EvohomeService(settings)
    controller = EvohomeController(evohome_service, weather, settings)

    controller.validate_configuration()

    return Application(settings, evohome_service, controller, presence, homeassistant)


async def main() -> None:
    settings = Settings.load()
    app = build_application(settings)
    await app.run()


if __name__ == "__main__":
    asyncio.run(main())
