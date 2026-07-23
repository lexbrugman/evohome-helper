import asyncio
import os
import signal

import pytest

from dataclasses import replace
from freezegun import freeze_time
from unittest.mock import AsyncMock, Mock

from evohomeasync2 import SystemMode

import main

from evohome_helper.evohome import EvohomeController


def _grace_schedule(evohome_factory, *, in_grace):
    if in_grace:
        return evohome_factory.uniform_schedule(20, "07:55:00")
    return evohome_factory.uniform_schedule(20, "06:00:00")


def _fake_presence(*, someone_home=False, away_grace=False, known=True):
    presence = Mock()
    presence.is_someone_home = AsyncMock(return_value=someone_home)
    presence.is_in_away_grace_period = AsyncMock(return_value=away_grace)
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
    "someone_home,away_grace_active,schedule_grace_active,start_mode,expected_mode,clock",
    [
        (True, True, False, SystemMode.AUTO_WITH_ECO, SystemMode.AUTO, "2024-04-10 08:00:00"),
        (False, True, True, SystemMode.AUTO_WITH_ECO, SystemMode.AUTO, "2024-04-07 08:00:00"),
        (False, False, False, SystemMode.AUTO, SystemMode.AWAY, "2024-04-10 08:00:00"),
    ],
)
async def test_set_thermostat_mode_public_scenarios(
    make_service,
    evohome_factory,
    settings,
    someone_home,
    away_grace_active,
    schedule_grace_active,
    start_mode,
    expected_mode,
    clock,
):
    state = evohome_factory.complete_state(system_mode=start_mode, schedule=_grace_schedule(evohome_factory, in_grace=schedule_grace_active))
    service = make_service(locations=[state.location])
    presence = _fake_presence(someone_home=someone_home, away_grace=away_grace_active, known=True)
    app = _build_app(settings, service, presence)

    with freeze_time(clock):
        await app.determine_and_set_thermostat_mode()

    state.control_system.set_mode.assert_awaited_once_with(expected_mode)
    assert state.control_system.mode == expected_mode


async def test_set_thermostat_mode_multiple_zones_keeps_normal(make_service, evohome_factory, settings):
    early_zone = evohome_factory.zone(name="Hall", schedule=evohome_factory.uniform_schedule(19, "06:00:00"))
    grace_zone = evohome_factory.zone(name="Living", schedule=evohome_factory.uniform_schedule(21, "07:55:00"))

    control_system = evohome_factory.control_system(mode=SystemMode.AUTO_WITH_ECO, zones=[early_zone, grace_zone])
    location = evohome_factory.location(control_systems=[control_system])
    service = make_service(locations=[location])
    presence = _fake_presence(someone_home=False, away_grace=True, known=True)
    app = _build_app(settings, service, presence)

    with freeze_time("2024-04-07 08:00:00"):
        await app.determine_and_set_thermostat_mode()

    control_system.set_mode.assert_awaited_once_with(SystemMode.AUTO)
    assert control_system.mode == SystemMode.AUTO


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


async def test_run_resets_evohome_client_after_repeated_failures(settings):
    app = _minimal_app(settings, interval=0.01)
    app.determine_and_set_thermostat_mode = AsyncMock(side_effect=Exception("boom"))

    task = asyncio.create_task(app.run())
    async with asyncio.timeout(2):
        while app._evohome_service.reset.await_count == 0:
            await asyncio.sleep(0.01)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, timeout=2)

    assert app.determine_and_set_thermostat_mode.await_count >= main.CONSECUTIVE_FAILURES_BEFORE_RESET
