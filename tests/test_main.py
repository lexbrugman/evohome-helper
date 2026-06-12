import asyncio
import os
import signal

import pytest
from freezegun import freeze_time
from unittest.mock import AsyncMock

from evohomeasync2.schemas import SystemMode

import main


def _patch_normal_mode_conditions(monkeypatch):
    monkeypatch.setattr("settings.AUTO_ECO_ENABLED", True)
    monkeypatch.setattr("settings.AUTO_ECO_OUTSIDE_TEMP_THRESHOLD", 14)
    monkeypatch.setattr("settings.AUTO_ECO_INSIDE_TEMP_DIFF", 2)
    monkeypatch.setattr("evohome_helper.evohome.weather.get_current_temperature", AsyncMock(return_value=10))


def _grace_schedule(evohome_factory, *, in_grace):
    if in_grace:
        return evohome_factory.uniform_schedule(20, "07:55:00")
    return evohome_factory.uniform_schedule(20, "06:00:00")


@pytest.mark.parametrize(
    "someone_home,away_grace_active,schedule_grace_active,start_mode,expected_mode,clock",
    [
        (True, True, False, SystemMode.AUTO_WITH_ECO, SystemMode.AUTO, "2024-04-10 08:00:00"),
        (False, True, True, SystemMode.AUTO_WITH_ECO, SystemMode.AUTO, "2024-04-07 08:00:00"),
        (False, False, False, SystemMode.AUTO, SystemMode.AWAY, "2024-04-10 08:00:00"),
    ],
)
async def test_set_thermostat_mode_public_scenarios(
    monkeypatch,
    installed_evohome_client,
    evohome_factory,
    someone_home,
    away_grace_active,
    schedule_grace_active,
    start_mode,
    expected_mode,
    clock,
):
    state = installed_evohome_client(system_mode=start_mode, schedule=_grace_schedule(evohome_factory, in_grace=schedule_grace_active))
    monkeypatch.setattr("settings.EVOHOME_LOCATION_NAME", state.location.name)
    _patch_normal_mode_conditions(monkeypatch)

    monkeypatch.setattr("evohome_helper.presence.is_someone_home", AsyncMock(return_value=someone_home))
    monkeypatch.setattr("evohome_helper.presence.is_in_away_grace_period", AsyncMock(return_value=away_grace_active))

    with freeze_time(clock):
        await main.determine_and_set_thermostat_mode()

    state.control_system.set_mode.assert_awaited_once_with(expected_mode)
    assert state.control_system.mode == expected_mode


async def test_set_thermostat_mode_multiple_zones_keeps_normal(monkeypatch, evohome_factory):
    early_zone = evohome_factory.zone(name="Hall", schedule=evohome_factory.uniform_schedule(19, "06:00:00"))
    grace_zone = evohome_factory.zone(name="Living", schedule=evohome_factory.uniform_schedule(21, "07:55:00"))

    control_system = evohome_factory.control_system(
        mode=SystemMode.AUTO_WITH_ECO,
        zones=[early_zone, grace_zone],
    )
    location = evohome_factory.location(control_systems=[control_system])

    monkeypatch.setattr("evohome_helper.evohome_client.get_location", AsyncMock(return_value=location))
    _patch_normal_mode_conditions(monkeypatch)
    monkeypatch.setattr("evohome_helper.presence.is_someone_home", AsyncMock(return_value=False))
    monkeypatch.setattr("evohome_helper.presence.is_in_away_grace_period", AsyncMock(return_value=True))

    with freeze_time("2024-04-07 08:00:00"):
        await main.determine_and_set_thermostat_mode()

    control_system.set_mode.assert_awaited_once_with(SystemMode.AUTO)
    assert control_system.mode == SystemMode.AUTO


async def test_main_shuts_down_gracefully_on_sigterm(monkeypatch):
    monkeypatch.setattr(main, "determine_and_set_thermostat_mode", AsyncMock())
    monkeypatch.setattr("settings.INTERVAL", 60)

    task = asyncio.create_task(main.main())
    await asyncio.sleep(0.05)
    os.kill(os.getpid(), signal.SIGTERM)

    await asyncio.wait_for(task, timeout=2)
    main.determine_and_set_thermostat_mode.assert_awaited()


async def test_main_resets_evohome_client_after_repeated_failures(monkeypatch):
    monkeypatch.setattr(main, "determine_and_set_thermostat_mode", AsyncMock(side_effect=Exception("boom")))
    reset_mock = AsyncMock()
    monkeypatch.setattr("evohome_helper.evohome_client.reset_client", reset_mock)
    monkeypatch.setattr("settings.INTERVAL", 0.01)

    task = asyncio.create_task(main.main())
    async with asyncio.timeout(2):
        while reset_mock.await_count == 0:
            await asyncio.sleep(0.01)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, timeout=2)

    assert main.determine_and_set_thermostat_mode.await_count >= main.CONSECUTIVE_FAILURES_BEFORE_RESET
