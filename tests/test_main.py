import asyncio
import logging
import os
import signal

import pytest

from dataclasses import replace
from freezegun import freeze_time
from unittest.mock import AsyncMock, Mock

from evohomeasync2 import SystemMode
from evohomeasync2.exceptions import ApiCallFailedError, BadUserCredentialsError, InvalidSystemModeError

import main

from evohome_helper.evohome_client import LocationNotFound

from evohome_helper.evohome import EvohomeController


def _preheat_schedule(evohome_factory, *, in_window):
    if in_window:
        return evohome_factory.preheat_schedule(20, "07:55:00")
    return evohome_factory.preheat_schedule(20, "06:00:00")


def _fake_presence(*, someone_home=False, recently_home=False, just_left=False, known=True):
    presence = Mock()
    presence.refresh = AsyncMock()
    presence.is_someone_home = Mock(return_value=someone_home if known else None)
    presence.was_someone_home_recently = Mock(return_value=recently_home)
    presence.did_someone_leave_just_now = Mock(return_value=just_left)
    return presence


def _fake_arrival_listener(*, arrivals=0):
    """Reports the given number of arrivals as soon as the loop starts, then keeps
    listening until it is cancelled, as the real listener does."""
    listener = Mock()

    async def run(on_arrival):
        for _ in range(arrivals):
            on_arrival()
        await asyncio.Event().wait()

    listener.run = Mock(side_effect=run)
    return listener


def _build_app(settings, service, presence, outside_temp=10):
    weather = Mock()
    weather.get_current_temperature = AsyncMock(return_value=outside_temp)
    controller = EvohomeController(service, settings)
    homeassistant = Mock()
    homeassistant.close = AsyncMock()
    return main.Application(settings, service, controller, presence, weather, homeassistant, _fake_arrival_listener())


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


async def test_thermostat_unchanged_when_someone_left_just_now(make_service, evohome_factory, settings):
    state = evohome_factory.complete_state(system_mode=SystemMode.AUTO)
    service = make_service(locations=[state.location])
    presence = _fake_presence(someone_home=False, recently_home=True, just_left=True)
    app = _build_app(settings, service, presence)

    with freeze_time("2024-04-10 08:00:00"):
        await app.determine_and_set_thermostat_mode()

    state.control_system.set_mode.assert_not_awaited()


async def test_set_thermostat_mode_uses_eco_when_warm_outside(make_service, evohome_factory, settings):
    """The weather reading reaches the policy: warm outside with someone home means eco."""
    state = evohome_factory.complete_state(system_mode=SystemMode.AUTO, setpoint=20)
    service = make_service(locations=[state.location])
    config = replace(settings, auto_eco_enabled=True, auto_eco_outside_temp_threshold=14, auto_eco_inside_temp_diff=2)
    app = _build_app(config, service, _fake_presence(someone_home=True), outside_temp=20)

    with freeze_time("2024-04-10 08:00:00"):
        await app.determine_and_set_thermostat_mode()

    state.control_system.set_mode.assert_awaited_once_with(SystemMode.AUTO_WITH_ECO)


async def test_set_thermostat_mode_skips_the_weather_when_auto_eco_is_disabled(make_service, evohome_factory, settings):
    state = evohome_factory.complete_state(system_mode=SystemMode.AUTO_WITH_ECO)
    service = make_service(locations=[state.location])
    app = _build_app(replace(settings, auto_eco_enabled=False), service, _fake_presence(someone_home=True))

    with freeze_time("2024-04-10 08:00:00"):
        await app.determine_and_set_thermostat_mode()

    app._weather.get_current_temperature.assert_not_awaited()
    state.control_system.set_mode.assert_awaited_once_with(SystemMode.AUTO)


def _minimal_app(settings, *, interval, arrivals=0):
    service = Mock()
    service.close = AsyncMock()
    service.reset = AsyncMock()
    homeassistant = Mock()
    homeassistant.close = AsyncMock()
    listener = _fake_arrival_listener(arrivals=arrivals)
    return main.Application(replace(settings, interval=interval), service, Mock(), Mock(), Mock(), homeassistant, listener)


async def _run_cycles(app, outcomes):
    """Run the loop for one cycle per outcome -- an exception to fail with, a callable to
    call, or None to simply succeed -- and shut it down during the last one; returns how
    long that took."""
    outcomes = list(outcomes)

    async def cycle():
        outcome = outcomes.pop(0)
        if not outcomes:
            os.kill(os.getpid(), signal.SIGTERM)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is not None:
            outcome()

    app.determine_and_set_thermostat_mode = AsyncMock(side_effect=cycle)
    started = asyncio.get_running_loop().time()

    await asyncio.wait_for(app.run(), timeout=5)

    assert not outcomes
    return asyncio.get_running_loop().time() - started


async def test_run_starts_a_cycle_early_when_someone_arrives(settings, monkeypatch):
    monkeypatch.setattr(main, "MINIMUM_CYCLE_GAP", 0.01)
    app = _minimal_app(settings, interval=60, arrivals=1)

    # the second cycle would otherwise be a minute away
    await _run_cycles(app, [None, None])


async def test_run_starts_another_cycle_for_an_arrival_during_a_cycle(settings, monkeypatch):
    # that cycle may have read presence before the arrival
    monkeypatch.setattr(main, "MINIMUM_CYCLE_GAP", 0.01)
    app = _minimal_app(settings, interval=60)

    await _run_cycles(app, [app._someone_arrived.set, None])


async def test_run_keeps_the_minimum_gap_between_cycles_when_someone_arrives(settings, monkeypatch):
    # a flapping tracker must not burn the vendor's rate limit
    monkeypatch.setattr(main, "MINIMUM_CYCLE_GAP", 0.2)
    app = _minimal_app(settings, interval=60, arrivals=5)

    assert await _run_cycles(app, [None, None]) >= 0.2


async def test_run_waits_out_the_interval_when_no_one_arrives(settings, monkeypatch):
    monkeypatch.setattr(main, "MINIMUM_CYCLE_GAP", 0.01)
    app = _minimal_app(settings, interval=0.2)

    assert await _run_cycles(app, [None, None]) >= 0.2


async def test_run_stops_the_arrival_listener_on_shutdown(settings):
    app = _minimal_app(settings, interval=60)
    stopped = asyncio.Event()

    async def run(on_arrival):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    app._arrival_listener.run = run

    await _run_cycles(app, [None])

    assert stopped.is_set()


async def test_run_shuts_down_gracefully_on_sigterm(settings):
    app = _minimal_app(settings, interval=60)

    await _run_cycles(app, [None])

    # the finally block must release both aiohttp sessions
    app._homeassistant.close.assert_awaited_once()
    app._evohome_service.close.assert_awaited_once()


async def test_run_resets_only_after_consecutive_failures(settings):
    """Two failures, a success, then three failures: the success must break the streak,
    so exactly one reset fires, and only after the three consecutive failures."""
    app = _minimal_app(settings, interval=0.01)
    boom = ApiCallFailedError("boom")
    cycles_before_reset = []
    app._evohome_service.reset = AsyncMock(side_effect=lambda: cycles_before_reset.append(app.determine_and_set_thermostat_mode.await_count))

    await _run_cycles(app, [boom, boom, None, boom, boom, boom])

    assert cycles_before_reset == [6]


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

    await _run_cycles(app, [error] * (main.CONSECUTIVE_FAILURES_BEFORE_RESET + 1))

    app._evohome_service.reset.assert_not_awaited()


async def test_run_logs_a_transient_failure_as_one_line_and_a_bug_with_its_traceback(settings, caplog):
    app = _minimal_app(settings, interval=0.01)

    await _run_cycles(app, [ApiCallFailedError("temporary failure", status=503), ValueError("bug")])

    failures = [record for record in caplog.records if record.name == main.logger.name and record.levelno >= logging.WARNING]
    assert [(record.levelno, record.exc_info is not None) for record in failures] == [(logging.WARNING, False), (logging.ERROR, True)]
    assert "ApiCallFailedError: temporary failure" in failures[0].message


@pytest.mark.parametrize(
    "error",
    [
        LocationNotFound("Nowhere"),  # a misconfigured location name
        BadUserCredentialsError("invalid_grant", status=400),  # rejected credentials
    ],
)
async def test_run_exits_on_a_misconfiguration_that_cannot_recover(settings, error):
    # retrying would never help, and for credentials would hammer the vendor's auth endpoint
    app = _minimal_app(settings, interval=60)
    app.determine_and_set_thermostat_mode = AsyncMock(side_effect=error)

    with pytest.raises(type(error)):
        await asyncio.wait_for(app.run(), timeout=2)

    app._evohome_service.reset.assert_not_awaited()
    # the finally block must still release both aiohttp sessions
    app._homeassistant.close.assert_awaited_once()
    app._evohome_service.close.assert_awaited_once()
