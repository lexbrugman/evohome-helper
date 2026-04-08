import pytest
from datetime import datetime
from freezegun import freeze_time
from unittest.mock import AsyncMock

from evohomeasync2.schemas import SystemMode, ZoneMode

from evohome_helper import evohome


def test_factory_complete_state_supports_full_state_setup(evohome_factory):
    state = evohome_factory.complete_state(zone_mode=ZoneMode.PERMANENT_OVERRIDE, system_mode=SystemMode.DAY_OFF, with_fault=True)

    assert state.control_system.mode == SystemMode.DAY_OFF
    assert state.zone.mode == ZoneMode.PERMANENT_OVERRIDE
    assert state.zone.active_faults == ["fault"]


def test_get_override_modes_excludes_expected_modes(monkeypatch):
    monkeypatch.setattr("settings.EVOHOME_AWAY_MODE", "away")

    modes = evohome._get_override_modes()

    assert SystemMode.AUTO not in modes
    assert SystemMode.AUTO_WITH_ECO not in modes
    assert SystemMode.AWAY not in modes
    assert SystemMode.HEATING_OFF in modes


def test_is_override_enabled_detects_system_mode_override(evohome_factory):
    state = evohome_factory.complete_state(system_mode=SystemMode.DAY_OFF)

    assert evohome._is_override_enabled(state.control_system) is True


def test_is_override_enabled_detects_manual_zone_setpoint(evohome_factory):
    state = evohome_factory.complete_state(zone_mode=ZoneMode.PERMANENT_OVERRIDE)

    assert evohome._is_override_enabled(state.control_system) is True


def test_is_override_enabled_false_when_schedule_followed(evohome_factory):
    state = evohome_factory.complete_state()

    assert evohome._is_override_enabled(state.control_system) is False


async def test_set_mode_skips_when_override_enabled(evohome_factory):
    state = evohome_factory.complete_state(zone_mode=ZoneMode.TEMPORARY_OVERRIDE)

    await evohome._set_mode(SystemMode.AUTO_WITH_ECO, state.location)

    state.control_system.set_mode.assert_not_awaited()


async def test_set_mode_skips_when_mode_already_set(evohome_factory):
    state = evohome_factory.complete_state(system_mode=SystemMode.AUTO_WITH_ECO)

    await evohome._set_mode(SystemMode.AUTO_WITH_ECO, state.location)

    state.control_system.set_mode.assert_not_awaited()


async def test_set_mode_updates_control_system_when_allowed(evohome_factory):
    state = evohome_factory.complete_state(system_mode=SystemMode.AUTO, zone_mode=ZoneMode.FOLLOW_SCHEDULE)

    await evohome._set_mode(SystemMode.AUTO_WITH_ECO, state.location)

    state.control_system.set_mode.assert_awaited_once_with(SystemMode.AUTO_WITH_ECO)


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
async def test_is_normal_heating_needed_paths(monkeypatch, evohome_factory, auto_eco_enabled, schedule_setpoint, outside_temp, expected):
    state = evohome_factory.complete_state(schedule=evohome_factory.uniform_schedule(setpoint=schedule_setpoint))

    monkeypatch.setattr("settings.AUTO_ECO_ENABLED", auto_eco_enabled)
    monkeypatch.setattr("settings.AUTO_ECO_OUTSIDE_TEMP_THRESHOLD", 14)
    monkeypatch.setattr("settings.AUTO_ECO_INSIDE_TEMP_DIFF", 2)
    monkeypatch.setattr("evohome_helper.evohome.weather.get_current_temperature", AsyncMock(return_value=outside_temp))

    with freeze_time("2024-04-10 08:00:00"):
        assert await evohome._is_normal_heating_needed(state.location) is expected


def test_is_in_schedule_grace_period(monkeypatch, evohome_factory):
    state = evohome_factory.complete_state(schedule=evohome_factory.uniform_schedule(20, "11:55:00"))

    monkeypatch.setattr("settings.PRESENCE_HEATING_SCHEDULE_GRACE_TIME", 900)

    with freeze_time("2024-04-07 12:00:00"):
        assert evohome.is_in_schedule_grace_period(state.location) is True


def test_is_in_schedule_grace_period_false_outside_window(monkeypatch, evohome_factory):
    state = evohome_factory.complete_state(schedule=evohome_factory.uniform_schedule(20, "06:00:00"))

    monkeypatch.setattr("settings.PRESENCE_HEATING_SCHEDULE_GRACE_TIME", 900)

    with freeze_time("2024-04-07 12:00:00"):
        assert evohome.is_in_schedule_grace_period(state.location) is False


def test_is_in_schedule_grace_period_skips_off_zones(monkeypatch, evohome_factory):
    # setpoint at or below EVOHOME_OFF_TEMP_THRESHOLD (5) should be skipped
    state = evohome_factory.complete_state(schedule=evohome_factory.uniform_schedule(5, "11:55:00"))

    monkeypatch.setattr("settings.PRESENCE_HEATING_SCHEDULE_GRACE_TIME", 900)
    monkeypatch.setattr("settings.EVOHOME_OFF_TEMP_THRESHOLD", 5)

    with freeze_time("2024-04-07 12:00:00"):
        assert evohome.is_in_schedule_grace_period(state.location) is False


def test_get_zone_switch_points_flattens_and_sorts(evohome_factory):
    sp_early = evohome_factory.switchpoint("07:00:00", 20)
    sp_late = evohome_factory.switchpoint("17:00:00", 20)
    daily = [evohome_factory.day_schedule(d, [sp_early, sp_late]) for d in range(7)]
    state = evohome_factory.complete_state(schedule=daily)

    now = datetime(2024, 4, 10, 18, 0, 0)  # Wednesday after both switchpoints
    switch_points = evohome._get_zone_switch_points(state.zone, now)

    # Should have 7 days × 2 switchpoints = 14 entries, sorted ascending
    assert len(switch_points) == 14
    datetimes = [dt for dt, _ in switch_points]
    assert datetimes == sorted(datetimes)


def test_get_zone_switch_points_week_boundary_sunday_to_monday(evohome_factory):
    """On Monday, Sunday switchpoints should map to yesterday, not next Sunday."""
    state = evohome_factory.complete_state(schedule=evohome_factory.uniform_schedule(20, "07:00:00"))

    now = datetime(2024, 4, 8, 8, 0, 0)  # Monday 2024-04-08
    switch_points = evohome._get_zone_switch_points(state.zone, now)

    sunday_sps = [(dt, temp) for dt, temp in switch_points if dt.weekday() == 6]
    assert len(sunday_sps) == 1
    assert sunday_sps[0][0] == datetime(2024, 4, 7, 7, 0, 0)  # last Sunday, not next


def test_get_active_setpoint_returns_none_when_zone_has_no_schedule(evohome_factory):
    state = evohome_factory.complete_state(schedule=[])

    now = datetime(2024, 4, 10, 8, 0, 0)
    assert evohome._get_active_setpoint(state.zone, now) is None


def test_get_highest_set_point_temp_returns_none_when_no_valid_setpoints(evohome_factory):
    state = evohome_factory.complete_state(schedule=[])

    with freeze_time("2024-04-10 08:00:00"):
        assert evohome._get_highest_set_point_temp(state.location) is None


async def test_is_normal_heating_needed_when_no_valid_active_setpoint(monkeypatch, evohome_factory):
    state = evohome_factory.complete_state(schedule=[])

    monkeypatch.setattr("settings.AUTO_ECO_ENABLED", True)
    monkeypatch.setattr("evohome_helper.evohome.weather.get_current_temperature", AsyncMock(return_value=25))

    with freeze_time("2024-04-10 08:00:00"):
        assert await evohome._is_normal_heating_needed(state.location) is True


def test_current_zone_switch_point_ignores_off_period(evohome_factory):
    """When the current scheduled period is off, return the last heating switchpoint."""
    sp_heat = evohome_factory.switchpoint("11:55:00", 20)
    sp_off = evohome_factory.switchpoint("12:05:00", 5)
    daily = [evohome_factory.day_schedule(d, [sp_heat, sp_off]) for d in range(7)]
    state = evohome_factory.complete_state(schedule=daily)

    now = datetime(2024, 4, 7, 12, 10, 0)  # Sunday, inside the "off" window
    sp_start, sp_temp = evohome._get_last_heating_switchpoint(state.zone, now)

    assert sp_temp == 20.0
    assert sp_start == datetime(2024, 4, 7, 11, 55, 0)


def test_get_last_heating_switchpoint_returns_none_when_zone_always_off(monkeypatch, evohome_factory):
    state = evohome_factory.complete_state(schedule=evohome_factory.uniform_schedule(15, "11:55:00"))
    monkeypatch.setattr("settings.EVOHOME_OFF_TEMP_THRESHOLD", 15)

    now = datetime(2024, 4, 7, 12, 10, 0)
    assert evohome._get_last_heating_switchpoint(state.zone, now) is None


def test_is_in_schedule_grace_period_triggered_after_off_within_grace(monkeypatch, evohome_factory):
    """Grace period uses last heating start even if current period is off."""
    sp_heat = evohome_factory.switchpoint("11:55:00", 20)
    sp_off = evohome_factory.switchpoint("12:05:00", 5)
    daily = [evohome_factory.day_schedule(d, [sp_heat, sp_off]) for d in range(7)]
    state = evohome_factory.complete_state(schedule=daily)

    monkeypatch.setattr("settings.PRESENCE_HEATING_SCHEDULE_GRACE_TIME", 900)
    monkeypatch.setattr("settings.EVOHOME_OFF_TEMP_THRESHOLD", 5)

    with freeze_time("2024-04-07 12:09:00"):  # 14 minutes after heat started, within 15-min grace
        assert evohome.is_in_schedule_grace_period(state.location) is True


def test_is_in_schedule_grace_period_false_when_zone_always_off(monkeypatch, evohome_factory):
    state = evohome_factory.complete_state(schedule=evohome_factory.uniform_schedule(15, "11:55:00"))
    monkeypatch.setattr("settings.PRESENCE_HEATING_SCHEDULE_GRACE_TIME", 900)
    monkeypatch.setattr("settings.EVOHOME_OFF_TEMP_THRESHOLD", 15)

    with freeze_time("2024-04-07 12:00:00"):
        assert evohome.is_in_schedule_grace_period(state.location) is False


def test_get_zones_filters_faulty_zones(evohome_factory):
    state = evohome_factory.complete_state(with_fault=False)
    faulty = evohome_factory.zone(name="bad", active_faults=["fault"])
    state.control_system.zones.append(faulty)

    zones = list(evohome.get_zones(state.location))

    assert zones == [state.zone]


async def test_get_location_uses_default_name(monkeypatch, evohome_client):
    monkeypatch.setattr("settings.EVOHOME_LOCATION_NAME", "MyHome")
    state = evohome_client(location_name="MyHome")

    loc = await evohome.get_location()

    assert loc is state.location


async def test_get_location_raises_for_missing_location(evohome_client):
    evohome_client(location_name="KnownLocation")

    with pytest.raises(evohome.LocationNotFound):
        await evohome.get_location("unknown")


async def test_set_normal_selects_auto_or_eco(monkeypatch, evohome_factory):
    monkeypatch.setattr("settings.AUTO_ECO_ENABLED", True)
    monkeypatch.setattr("settings.AUTO_ECO_OUTSIDE_TEMP_THRESHOLD", 14)
    monkeypatch.setattr("settings.AUTO_ECO_INSIDE_TEMP_DIFF", 2)

    with freeze_time("2024-04-10 08:00:00"):
        auto_state = evohome_factory.complete_state(system_mode=SystemMode.AUTO_WITH_ECO, schedule=evohome_factory.uniform_schedule(setpoint=20))
        monkeypatch.setattr("evohome_helper.evohome.weather.get_current_temperature", AsyncMock(return_value=10))
        await evohome.set_normal(auto_state.location)
        auto_state.control_system.set_mode.assert_awaited_once_with(SystemMode.AUTO)

        eco_state = evohome_factory.complete_state(system_mode=SystemMode.AUTO, schedule=evohome_factory.uniform_schedule(setpoint=20))
        monkeypatch.setattr("evohome_helper.evohome.weather.get_current_temperature", AsyncMock(return_value=20))
        await evohome.set_normal(eco_state.location)
        eco_state.control_system.set_mode.assert_awaited_once_with(SystemMode.AUTO_WITH_ECO)


async def test_set_away_uses_configured_away_mode(monkeypatch, evohome_factory):
    state = evohome_factory.complete_state(system_mode=SystemMode.AUTO)
    monkeypatch.setattr("settings.EVOHOME_AWAY_MODE", "custom")

    await evohome.set_away(state.location)

    state.control_system.set_mode.assert_awaited_once_with(SystemMode.CUSTOM)


async def test_client_returns_existing_client():
    fake_client = object()
    evohome._evohome_client = fake_client

    result = await evohome._client()

    assert result is fake_client


async def test_client_creates_and_initializes(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr("evohome_helper.evohome.EvohomeClient", lambda *a, **kw: mock_client)

    result = await evohome._client()

    mock_client.update.assert_awaited_once_with(dont_update_status=True)
    assert result is mock_client
