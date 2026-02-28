import main
import pytest

from evohome_helper import evohome
from evohome_helper.evohome import ThermostatStatus
from freezegun import freeze_time


def _prepare_common(monkeypatch, state):
    monkeypatch.setattr("settings.EVOHOME_LOCATION_NAME", state["location"].name)


def _prepare_normal_mode_conditions(monkeypatch, state):
    state["zone"].set_weekly_schedule(setpoint=20, time_of_day="07:00:00")
    monkeypatch.setattr("settings.AUTO_ECO_ENABLED", True)
    monkeypatch.setattr("settings.AUTO_ECO_OUTSIDE_TEMP_THRESHOLD", 14)
    monkeypatch.setattr("settings.AUTO_ECO_INSIDE_TEMP_DIFF", 2)
    monkeypatch.setattr("evohome_helper.evohome.weather.get_temperature", lambda: 10)


def _prepare_schedule_grace_state(state, *, in_grace):
    if in_grace:
        state["zone"].set_weekly_schedule(setpoint=20, time_of_day="07:55:00")
    else:
        state["zone"].set_weekly_schedule(setpoint=20, time_of_day="06:00:00")


@pytest.mark.parametrize(
    "someone_home,away_grace_active,schedule_grace_active,start_mode,expected_status,clock",
    [
        (True, True, False, ThermostatStatus.eco.mode, ThermostatStatus.auto.status, "2024-04-10 08:00:00"),
        (False, True, True, ThermostatStatus.eco.mode, ThermostatStatus.auto.status, "2024-04-07 08:00:00"),
        (False, False, False, ThermostatStatus.auto.mode, ThermostatStatus.away.status, "2024-04-10 08:00:00"),
    ],
)
def test_set_thermostat_mode_public_scenarios(
    monkeypatch,
    evohome_client,
    someone_home,
    away_grace_active,
    schedule_grace_active,
    start_mode,
    expected_status,
    clock,
):
    _client, state = evohome_client(system_mode=start_mode)
    _prepare_common(monkeypatch, state)
    _prepare_normal_mode_conditions(monkeypatch, state)
    _prepare_schedule_grace_state(state, in_grace=schedule_grace_active)

    monkeypatch.setattr("evohome_helper.presence.is_someone_home", lambda: someone_home)
    monkeypatch.setattr("evohome_helper.presence.is_in_away_grace_period", lambda: away_grace_active)

    with freeze_time(clock):
        main.set_thermostat_mode()

    state["control_system"].set_status.assert_called_once_with(expected_status)
    assert state["control_system"].systemModeStatus["mode"] == (
        ThermostatStatus.get_by_status(expected_status).mode
    )


def test_set_thermostat_mode_multiple_zones_keeps_normal(monkeypatch, evohome_factory):
    early_zone = evohome_factory.zone(name="Hall")
    early_zone.set_weekly_schedule(setpoint=19, time_of_day="06:00:00")
    grace_zone = evohome_factory.zone(name="Living")
    grace_zone.set_weekly_schedule(setpoint=21, time_of_day="07:55:00")

    control_system = evohome_factory.control_system(
        mode=ThermostatStatus.eco.mode,
        zones={"z1": early_zone, "z2": grace_zone},
    )
    location = evohome_factory.location(control_systems={"c1": control_system})

    monkeypatch.setattr("evohome_helper.evohome.get_location", lambda location_name=None: location)
    monkeypatch.setattr("settings.EVOHOME_LOCATION_NAME", location.name)
    monkeypatch.setattr("settings.AUTO_ECO_ENABLED", True)
    monkeypatch.setattr("settings.AUTO_ECO_OUTSIDE_TEMP_THRESHOLD", 14)
    monkeypatch.setattr("settings.AUTO_ECO_INSIDE_TEMP_DIFF", 2)
    monkeypatch.setattr("evohome_helper.evohome.weather.get_temperature", lambda: 10)
    monkeypatch.setattr("evohome_helper.presence.is_someone_home", lambda: False)
    monkeypatch.setattr("evohome_helper.presence.is_in_away_grace_period", lambda: True)

    with freeze_time("2024-04-07 08:00:00"):
        main.set_thermostat_mode()

    control_system.set_status.assert_called_once_with(ThermostatStatus.auto.status)
    assert control_system.systemModeStatus["mode"] == ThermostatStatus.auto.mode
