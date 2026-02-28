from datetime import datetime

import pytest
from freezegun import freeze_time

from evohome_helper import evohome


def test_factory_complete_state_supports_full_state_setup(evohome_factory):
    state = evohome_factory.complete_state(zone_mode="PermanentOverride", system_mode="DayOff", with_fault=True)

    assert state["control_system"].systemModeStatus["mode"] == "DayOff"
    assert state["zone"].setpointStatus["setpointMode"] == "PermanentOverride"
    assert state["zone"].activeFaults == ["fault"]


def test_get_override_modes_excludes_expected_modes(monkeypatch):
    monkeypatch.setattr("settings.EVOHOME_AWAY_MODE", "away")

    modes = evohome._get_override_modes()

    assert evohome.ThermostatStatus.auto not in modes
    assert evohome.ThermostatStatus.eco not in modes
    assert evohome.ThermostatStatus.away not in modes
    assert evohome.ThermostatStatus.off in modes


def test_is_override_enabled_detects_system_mode_override(evohome_factory):
    state = evohome_factory.complete_state(system_mode="DayOff")

    assert evohome._is_override_enabled(state["control_system"]) is True


def test_is_override_enabled_detects_manual_zone_setpoint(evohome_factory):
    state = evohome_factory.complete_state(zone_mode="PermanentOverride")

    assert evohome._is_override_enabled(state["control_system"]) is True


def test_is_override_enabled_false_when_schedule_followed(evohome_factory):
    state = evohome_factory.complete_state()

    assert evohome._is_override_enabled(state["control_system"]) is False


def test_set_mode_skips_when_override_enabled(evohome_factory):
    state = evohome_factory.complete_state(zone_mode="TemporaryOverride")

    evohome._set_mode(evohome.ThermostatStatus.eco, state["location"])

    state["control_system"].set_status.assert_not_called()


def test_set_mode_skips_when_mode_already_set(evohome_factory):
    state = evohome_factory.complete_state(system_mode="AutoWithEco")

    evohome._set_mode(evohome.ThermostatStatus.eco, state["location"])

    state["control_system"].set_status.assert_not_called()


def test_set_mode_updates_control_system_when_allowed(evohome_factory):
    state = evohome_factory.complete_state(system_mode="Auto", zone_mode="FollowSchedule")

    evohome._set_mode(evohome.ThermostatStatus.eco, state["location"])

    state["control_system"].set_status.assert_called_once_with(evohome.ThermostatStatus.eco.status)


@pytest.mark.parametrize(
    "auto_eco_enabled,schedule_setpoint,outside_temp,expected",
    [
        (False, 20, 30, True),
        (True, 5, 30, True),
        (True, 20, -99, True),
        (True, 20, 13, True),
        (True, 20, 20, False),
        (True, 20, 17, True),
    ],
)
def test_is_normal_heating_needed_paths(monkeypatch, evohome_factory, auto_eco_enabled, schedule_setpoint, outside_temp, expected):
    state = evohome_factory.complete_state()
    state["zone"].set_weekly_schedule(setpoint=schedule_setpoint)

    monkeypatch.setattr("settings.AUTO_ECO_ENABLED", auto_eco_enabled)
    monkeypatch.setattr("settings.AUTO_ECO_OUTSIDE_TEMP_THRESHOLD", 14)
    monkeypatch.setattr("settings.AUTO_ECO_INSIDE_TEMP_DIFF", 2)
    monkeypatch.setattr("evohome_helper.evohome.weather.get_temperature", lambda: outside_temp)

    with freeze_time("2024-04-10 08:00:00"):
        assert evohome._is_normal_heating_needed(state["location"]) is expected


def test_switch_point_to_datetime_maps_weekday_correctly(evohome_factory):
    with freeze_time("2024-04-11 08:00:00"):
        sunday_dt = evohome._switch_point_to_datetime(evohome_factory.switch_point_reference(6, "09:30:00"))
        monday_dt = evohome._switch_point_to_datetime(evohome_factory.switch_point_reference(0, "09:30:00"))

    assert sunday_dt.strftime("%w") == "0"
    assert monday_dt.strftime("%w") == "1"


def test_get_zone_switch_points_flattens_and_sorts(evohome_factory):
    zone = evohome_factory.zone()
    zone.set_schedule([
        evohome_factory.day_schedule(2, [evohome_factory.switchpoint("08:00:00", 20)]),
        evohome_factory.day_schedule(2, [evohome_factory.switchpoint("07:00:00", 19)]),
    ])

    with freeze_time("2024-04-07 12:00:00"):
        points = evohome._get_zone_switch_points(zone)

    assert [p["heatSetpoint"] for p in points] == [19, 20]


def test_current_zone_switch_point_ignores_duplicates_off_and_future(monkeypatch, evohome_factory):
    monkeypatch.setattr("settings.EVOHOME_OFF_TEMP_THRESHOLD", 5)

    zone = evohome_factory.zone()
    zone.set_schedule([
        evohome_factory.day_schedule(2, [
            evohome_factory.switchpoint("10:00:00", 5),
            evohome_factory.switchpoint("11:00:00", 19),
            evohome_factory.switchpoint("11:30:00", 19),
            evohome_factory.switchpoint("12:15:00", 21),
        ]),
    ])

    with freeze_time("2024-04-10 12:00:00"):
        point_time, point_temp = evohome._get_current_zone_switch_point_from_schedule(zone)

    assert point_time == datetime(2024, 4, 10, 11, 0, 0)
    assert point_temp == 19


def test_is_in_schedule_grace_period(monkeypatch, evohome_factory):
    state = evohome_factory.complete_state()
    state["zone"].set_weekly_schedule(setpoint=20, time_of_day="11:55:00")

    monkeypatch.setattr("settings.PRESENCE_HEATING_SCHEDULE_GRACE_TIME", 900)

    with freeze_time("2024-04-07 12:00:00"):
        assert evohome.is_in_schedule_grace_period(state["location"]) is True


def test_get_zones_filters_faulty_zones(evohome_factory):
    state = evohome_factory.complete_state(with_fault=False)
    faulty = evohome_factory.zone(name="bad", activeFaults=["fault"])
    state["control_system"].zones["z2"] = faulty

    zones = list(evohome.get_zones(state["location"]))

    assert zones == [state["zone"]]


def test_get_location_uses_default_name(monkeypatch, evohome_client):
    monkeypatch.setattr("settings.EVOHOME_LOCATION_NAME", "MyHome")
    client, state = evohome_client(location_name="MyHome")

    loc = evohome.get_location()

    assert loc is state["location"]
    assert client.installation_calls == 1


def test_get_location_raises_for_missing_location(evohome_client):
    client, _state = evohome_client(location_name="KnownLocation")

    with pytest.raises(evohome.LocationNotFound):
        evohome.get_location("unknown")

    assert client.installation_calls == 1


def test_set_normal_selects_auto_or_eco(monkeypatch, evohome_factory):
    monkeypatch.setattr("settings.AUTO_ECO_ENABLED", True)
    monkeypatch.setattr("settings.AUTO_ECO_OUTSIDE_TEMP_THRESHOLD", 14)
    monkeypatch.setattr("settings.AUTO_ECO_INSIDE_TEMP_DIFF", 2)

    with freeze_time("2024-04-10 08:00:00"):
        auto_state = evohome_factory.complete_state(system_mode="Eco")
        auto_state["zone"].set_weekly_schedule(setpoint=20)
        monkeypatch.setattr("evohome_helper.evohome.weather.get_temperature", lambda: 10)
        evohome.set_normal(auto_state["location"])
        auto_state["control_system"].set_status.assert_called_once_with(evohome.ThermostatStatus.auto.status)

        eco_state = evohome_factory.complete_state(system_mode="Auto")
        eco_state["zone"].set_weekly_schedule(setpoint=20)
        monkeypatch.setattr("evohome_helper.evohome.weather.get_temperature", lambda: 20)
        evohome.set_normal(eco_state["location"])
        eco_state["control_system"].set_status.assert_called_once_with(evohome.ThermostatStatus.eco.status)


def test_set_away_uses_configured_away_mode(monkeypatch, evohome_factory):
    state = evohome_factory.complete_state(system_mode="Auto")
    monkeypatch.setattr("settings.EVOHOME_AWAY_MODE", "custom")

    evohome.set_away(state["location"])

    state["control_system"].set_status.assert_called_once_with(evohome.ThermostatStatus.custom.status)


def test_client_retries_and_succeeds(patch_evohome_client_class):
    class FlakyClient:
        attempts = 0

        def __init__(self, *_args, **_kwargs):
            FlakyClient.attempts += 1
            if FlakyClient.attempts < 3:
                raise KeyError("settings not ready")

    patch_evohome_client_class(FlakyClient)

    client = evohome._client()

    assert isinstance(client, FlakyClient)
    assert FlakyClient.attempts == 3


def test_client_raises_after_max_retries(patch_evohome_client_class):
    class BrokenClient:
        def __init__(self, *_args, **_kwargs):
            raise KeyError("always broken")

    patch_evohome_client_class(BrokenClient)

    with pytest.raises(KeyError):
        evohome._client()


def test_get_zone_switch_points_sorted_datetime_invariant(evohome_factory):
    zone = evohome_factory.zone()
    zone.set_schedule([
        evohome_factory.day_schedule(3, [evohome_factory.switchpoint("10:00:00", 20)]),
        evohome_factory.day_schedule(1, [evohome_factory.switchpoint("12:00:00", 20)]),
        evohome_factory.day_schedule(1, [evohome_factory.switchpoint("07:00:00", 19)]),
    ])

    with freeze_time("2024-04-07 12:00:00"):
        points = evohome._get_zone_switch_points(zone)

    datetimes = [p["DateTime"] for p in points]

    assert datetimes == sorted(datetimes)


def test_current_zone_switch_point_invariants(evohome_factory, monkeypatch):
    monkeypatch.setattr("settings.EVOHOME_OFF_TEMP_THRESHOLD", 5)

    zone = evohome_factory.zone()
    zone.set_schedule([
        evohome_factory.day_schedule(2, [
            evohome_factory.switchpoint("08:00:00", 5),
            evohome_factory.switchpoint("09:00:00", 18),
            evohome_factory.switchpoint("11:00:00", 18),
            evohome_factory.switchpoint("13:00:00", 20),
        ]),
    ])

    with freeze_time("2024-04-10 12:00:00"):
        point_time, point_temp = evohome._get_current_zone_switch_point_from_schedule(zone)

    assert point_time <= datetime(2024, 4, 10, 12, 0, 0)
    assert point_temp == 18
