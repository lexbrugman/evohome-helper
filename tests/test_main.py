import asyncio
import os
import signal

import pytest

from dataclasses import replace
from freezegun import freeze_time
from unittest.mock import AsyncMock, Mock

from evohomeasync2 import SystemMode
from evohomeasync2.exceptions import ApiCallFailedError, InvalidSystemModeError

import main

from evohome_helper.evohome_client import LocationNotFound

from evohome_helper.evohome import EvohomeController


def _preheat_schedule(evohome_factory, *, in_window):
    if in_window:
        return evohome_factory.preheat_schedule(20, "07:55:00")
    return evohome_factory.preheat_schedule(20, "06:00:00")


def _fake_presence(*, someone_home=False, recently_home=False, known=True):
    presence = Mock()
    presence.refresh = AsyncMock()
    presence.is_someone_home = Mock(return_value=someone_home)
    presence.was_someone_home_recently = Mock(return_value=recently_home)
    presence.is_presence_known = Mock(return_value=known)
    return presence


def _build_app(settings, service, presence):
    weather = Mock()
    weather.get_current_temperature = AsyncMock(return_value=10)
    controller = EvohomeController(service, weather, settings)
    homeassistant = Mock()
    homeassistant.close = AsyncMock()
    return main.Application(settings, service, controller, presence, homeassistant)


@pytest.mark.parametrize(
    "someone_home,recently_home,preheat_window,start_mode,expected_mode,clock",
    [
        (True, True, False, SystemMode.AUTO_WITH_ECO, SystemMode.AUTO, "2024-04-10 08:00:00"),
        (False, True, True, SystemMode.AUTO_WITH_ECO, SystemMode.AUTO, "2024-04-07 08:00:00"),
        (False, False, False, SystemMode.AUTO, SystemMode.AWAY, "2024-04-10 08:00:00"),
        # exactly one condition active must still mean away: both conjuncts are load-bearing
        (False, True, False, SystemMode.AUTO, SystemMode.AWAY, "2024-04-10 08:00:00"),
        (False, False, True, SystemMode.AUTO, SystemMode.AWAY, "2024-04-07 08:00:00"),
    ],
)
async def test_set_thermostat_mode_public_scenarios(
    make_service,
    evohome_factory,
    settings,
    someone_home,
    recently_home,
    preheat_window,
    start_mode,
    expected_mode,
    clock,
):
    state = evohome_factory.complete_state(system_mode=start_mode, schedule=_preheat_schedule(evohome_factory, in_window=preheat_window))
    service = make_service(locations=[state.location])
    presence = _fake_presence(someone_home=someone_home, recently_home=recently_home, known=True)
    app = _build_app(settings, service, presence)

    with freeze_time(clock):
        await app.determine_and_set_thermostat_mode()

    presence.refresh.assert_awaited_once()
    state.control_system.set_mode.assert_awaited_once_with(expected_mode)
    assert state.control_system.mode == expected_mode


async def test_set_thermostat_mode_multiple_zones_keeps_normal(make_service, evohome_factory, settings):
    early_zone = evohome_factory.zone(name="Hall", schedule=evohome_factory.preheat_schedule(19, "06:00:00"))
    preheat_zone = evohome_factory.zone(name="Living", schedule=evohome_factory.preheat_schedule(21, "07:55:00"))

    control_system = evohome_factory.control_system(mode=SystemMode.AUTO_WITH_ECO, zones=[early_zone, preheat_zone])
    location = evohome_factory.location(control_systems=[control_system])
    service = make_service(locations=[location])
    presence = _fake_presence(someone_home=False, recently_home=True, known=True)
    app = _build_app(settings, service, presence)

    with freeze_time("2024-04-07 08:00:00"):
        await app.determine_and_set_thermostat_mode()

    control_system.set_mode.assert_awaited_once_with(SystemMode.AUTO)
    assert control_system.mode == SystemMode.AUTO


async def test_set_thermostat_mode_survives_unavailable_zone_sensor(make_service, evohome_factory, settings):
    """An offline sensor reports {"is_available": False} with NO temperature key; the
    informational log line must not abort the control cycle."""
    state = evohome_factory.complete_state(system_mode=SystemMode.AUTO_WITH_ECO)
    state.zone.temperature_status = {"is_available": False}
    service = make_service(locations=[state.location])
    presence = _fake_presence(someone_home=True, known=True)
    app = _build_app(settings, service, presence)

    with freeze_time("2024-04-10 08:00:00"):
        await app.determine_and_set_thermostat_mode()

    state.control_system.set_mode.assert_awaited_once_with(SystemMode.AUTO)


async def test_thermostat_unchanged_when_presence_unknown(make_service, evohome_factory, settings):
    state = evohome_factory.complete_state(system_mode=SystemMode.AUTO)
    service = make_service(locations=[state.location])
    presence = _fake_presence(someone_home=False, known=False)
    app = _build_app(settings, service, presence)

    await app.determine_and_set_thermostat_mode()

    state.control_system.set_mode.assert_not_awaited()


def _minimal_app(settings, *, interval):
    service = Mock()
    service.close = AsyncMock()
    service.reset = AsyncMock()
    homeassistant = Mock()
    homeassistant.close = AsyncMock()
    return main.Application(replace(settings, interval=interval), service, Mock(), Mock(), homeassistant)


async def test_run_shuts_down_gracefully_on_sigterm(settings):
    app = _minimal_app(settings, interval=60)
    app.determine_and_set_thermostat_mode = AsyncMock()

    task = asyncio.create_task(app.run())
    await asyncio.sleep(0.05)
    os.kill(os.getpid(), signal.SIGTERM)

    await asyncio.wait_for(task, timeout=2)
    app.determine_and_set_thermostat_mode.assert_awaited()
    # the finally block must release both aiohttp sessions
    app._homeassistant.close.assert_awaited_once()
    app._evohome_service.close.assert_awaited_once()


async def test_run_resets_only_after_consecutive_failures(settings):
    """Two failures, a success, then three failures: the success must break the streak,
    so exactly one reset fires, and only after the three consecutive failures."""
    app = _minimal_app(settings, interval=0.01)
    log = []
    boom = ApiCallFailedError("boom")
    outcomes = [boom, boom, None, boom, boom, boom]

    async def scripted():
        if not outcomes:  # script exhausted: stop the loop
            os.kill(os.getpid(), signal.SIGTERM)
            return
        log.append("cycle")
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome

    app.determine_and_set_thermostat_mode = scripted
    app._evohome_service.reset = AsyncMock(side_effect=lambda: log.append("reset"))

    await asyncio.wait_for(app.run(), timeout=5)

    assert log == ["cycle"] * 6 + ["reset"]


@pytest.mark.parametrize(
    "error",
    [
        ValueError("bug"),  # a bug or bad Home Assistant data
        InvalidSystemModeError("unsupported system_mode"),  # permanent: fails identically every time
        ApiCallFailedError("rate limited", status=429),  # re-authenticating deepens the throttle
    ],
)
async def test_run_does_not_reset_on_errors_a_reset_cannot_cure(settings, error):
    """Only transient failures may churn the rate-limited vendor API with a re-authentication."""
    app = _minimal_app(settings, interval=0.01)
    outcomes = [error] * (main.CONSECUTIVE_FAILURES_BEFORE_RESET + 1)

    async def scripted():
        if not outcomes:
            os.kill(os.getpid(), signal.SIGTERM)
            return
        raise outcomes.pop(0)

    app.determine_and_set_thermostat_mode = scripted

    await asyncio.wait_for(app.run(), timeout=5)

    app._evohome_service.reset.assert_not_awaited()


async def test_run_exits_when_the_location_does_not_exist(settings):
    # a misconfigured location name can never recover on its own
    app = _minimal_app(settings, interval=60)
    app.determine_and_set_thermostat_mode = AsyncMock(side_effect=LocationNotFound("Nowhere"))

    with pytest.raises(LocationNotFound):
        await asyncio.wait_for(app.run(), timeout=2)

    app._evohome_service.reset.assert_not_awaited()
    # the finally block must still release both aiohttp sessions
    app._homeassistant.close.assert_awaited_once()
    app._evohome_service.close.assert_awaited_once()


async def test_run_resets_evohome_client_after_repeated_failures(settings):
    app = _minimal_app(settings, interval=0.01)
    app.determine_and_set_thermostat_mode = AsyncMock(side_effect=ApiCallFailedError("boom"))

    task = asyncio.create_task(app.run())
    async with asyncio.timeout(2):
        while app._evohome_service.reset.await_count == 0:
            await asyncio.sleep(0.01)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, timeout=2)

    assert app.determine_and_set_thermostat_mode.await_count >= main.CONSECUTIVE_FAILURES_BEFORE_RESET
