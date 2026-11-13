#!/usr/bin/env python

import asyncio
import logging
import os
import signal

from collections.abc import AsyncIterator, Coroutine, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from logging import config as log_config

from evohome_helper.arrivals import ArrivalListener
from evohome_helper.evohome import EvohomeController
from evohome_helper.evohome_client import UNRECOVERABLE_ERRORS, EvohomeService, is_transient_error
from evohome_helper.homeassistant import HomeAssistantClient
from evohome_helper.policy import Situation, decide
from evohome_helper.presence import PresenceTracker
from evohome_helper.weather import WeatherService
from settings import Settings

log_config.fileConfig(os.path.join(os.path.dirname(__file__), "logging.conf"))
logger = logging.getLogger(__name__)

CONSECUTIVE_FAILURES_BEFORE_RESET = 3
# an arrival starts a cycle early, but never sooner than this after the previous one: a
# tracker flapping between home and away must not burn the vendor's tight API rate limit
MINIMUM_CYCLE_GAP = 30


@contextmanager
def _shutdown_signals() -> Iterator[asyncio.Event]:
    """An event that is set when the process is asked to stop."""
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)

    for shutdown_signal in signals:
        loop.add_signal_handler(shutdown_signal, shutdown_event.set)
    try:
        yield shutdown_event
    finally:
        for shutdown_signal in signals:
            loop.remove_signal_handler(shutdown_signal)


@asynccontextmanager
async def _in_background(coroutine: Coroutine) -> AsyncIterator[None]:
    """Run the coroutine alongside the block, and cancel it when the block ends."""
    task = asyncio.create_task(coroutine)
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _wait_for_any(timeout: float, *events: asyncio.Event) -> None:
    waiters = [asyncio.ensure_future(event.wait()) for event in events]
    try:
        await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()


class Application:
    def __init__(
        self,
        settings: Settings,
        evohome_service: EvohomeService,
        controller: EvohomeController,
        presence: PresenceTracker,
        weather: WeatherService,
        homeassistant: HomeAssistantClient,
        arrival_listener: ArrivalListener,
    ):
        self._settings = settings
        self._evohome_service = evohome_service
        self._controller = controller
        self._presence = presence
        self._weather = weather
        self._homeassistant = homeassistant
        self._arrival_listener = arrival_listener
        self._someone_arrived = asyncio.Event()
        self._consecutive_failures = 0

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

        schedule = self._controller.get_schedule_summary(location)
        situation = Situation(
            someone_home=self._presence.is_someone_home(),
            seconds_since_last_departure=self._presence.seconds_since_last_departure(),
            seconds_since_scheduled_increase=schedule.seconds_since_increase,
            highest_scheduled_setpoint=schedule.highest_setpoint,
            # fetched before deciding so the policy stays free of I/O; only auto-eco uses
            # it, so this costs one local Home Assistant call per cycle that the away and
            # unchanged decisions ignore (and, during a weather outage, up to the client's
            # request timeout per cycle)
            outside_temperature=await self._weather.get_current_temperature() if self._settings.auto_eco_enabled else None,
        )

        decision = decide(situation, self._settings)
        logger.info(decision.reason)

        if decision.mode is not None:
            await self._controller.apply(decision.mode, location)

    async def run(self) -> None:
        try:
            with _shutdown_signals() as shutdown_event:
                async with _in_background(self._arrival_listener.run(self._on_arrival)):
                    while not shutdown_event.is_set():
                        # cleared before the cycle reads presence, not after: an arrival
                        # during the cycle may have been missed by it and must start another one
                        self._someone_arrived.clear()

                        await self._run_cycle()
                        await self._wait_for_next_cycle(shutdown_event)
        finally:
            logger.info("shutting down")

            await self._homeassistant.close()
            await self._evohome_service.close()

    def _on_arrival(self) -> None:
        # an arrival at a home that already had someone in it changes nothing, so it is
        # not worth a cycle (and its vendor API call); the next regular one will do
        if not self._presence.is_someone_home():
            self._someone_arrived.set()

    async def _run_cycle(self) -> None:
        """Run one cycle. Its failure is logged and survived, so the loop carries on --
        except a misconfiguration, which is raised to end it."""
        try:
            await self.determine_and_set_thermostat_mode()
        except UNRECOVERABLE_ERRORS as error:
            # exit so a misconfiguration is visible instead of retried forever
            logger.critical("%s; exiting", error)
            raise
        except Exception as error:
            if not is_transient_error(error):
                logger.exception("error in loop")
                return

            # expected during an outage, and the retries already logged their attempts:
            # one line, no traceback
            logger.warning("cycle failed: %s: %s", type(error).__name__, error)
            await self._count_failure()
        else:
            self._consecutive_failures = 0

    async def _count_failure(self) -> None:
        # only failures that recreating the evohome client can plausibly cure (a wedged
        # session, a token the library failed to refresh) count toward a reset; permanent
        # errors, rate limiting and bugs would just churn the heavily rate-limited vendor
        # API with pointless re-authentications
        self._consecutive_failures += 1

        if self._consecutive_failures >= CONSECUTIVE_FAILURES_BEFORE_RESET:
            logger.warning("resetting the evohome client after %d consecutive failures", self._consecutive_failures)
            await self._evohome_service.reset()
            self._consecutive_failures = 0

    async def _wait_for_next_cycle(self, shutdown_event: asyncio.Event) -> None:
        """Sleep out the interval, or until someone arrives home (but then at least the
        minimum gap between cycles), or until shutdown."""
        minimum_gap = min(MINIMUM_CYCLE_GAP, self._settings.interval)

        await _wait_for_any(minimum_gap, shutdown_event)
        if shutdown_event.is_set():
            return

        await _wait_for_any(self._settings.interval - minimum_gap, shutdown_event, self._someone_arrived)
        if self._someone_arrived.is_set():
            logger.info("someone arrived home; not waiting for the next interval")


def build_application(settings: Settings) -> Application:
    homeassistant = HomeAssistantClient(settings)
    presence = PresenceTracker(homeassistant, settings)
    weather = WeatherService(homeassistant, settings)
    evohome_service = EvohomeService(settings)
    controller = EvohomeController(evohome_service, settings)
    arrival_listener = ArrivalListener(homeassistant, settings)

    return Application(settings, evohome_service, controller, presence, weather, homeassistant, arrival_listener)


async def main() -> None:
    settings = Settings.load()
    app = build_application(settings)
    await app.run()


if __name__ == "__main__":
    asyncio.run(main())
