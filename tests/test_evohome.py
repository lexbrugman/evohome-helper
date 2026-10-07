import pytest

from dataclasses import replace
from datetime import datetime
from freezegun import freeze_time

from evohomeasync2 import FaultType, SystemMode, ZoneMode

from evohome_helper import evohome


def test_factory_complete_state_supports_full_state_setup(evohome_factory):
    state = evohome_factory.complete_state(zone_mode=ZoneMode.PERMANENT_OVERRIDE, system_mode=SystemMode.DAY_OFF, with_fault=True)

    assert state.control_system.mode == SystemMode.DAY_OFF
    assert state.zone.mode == ZoneMode.PERMANENT_OVERRIDE
    assert [fault["fault_type"] for fault in state.zone.active_faults] == [FaultType.ZON_S_CL]


def test_get_override_modes_excludes_expected_modes(controller_factory, settings):
    controller = controller_factory(config=replace(settings, evohome_away_mode="away"))

    modes = controller._get_override_modes()

    assert SystemMode.AUTO not in modes
    assert SystemMode.AUTO_WITH_ECO not in modes
    assert SystemMode.AWAY not in modes
    assert SystemMode.HEATING_OFF in modes


def test_is_override_enabled_detects_system_mode_override(controller_factory, evohome_factory):
    state = evohome_factory.complete_state(system_mode=SystemMode.DAY_OFF)

    assert controller_factory()._is_override_enabled(state.control_system) is True


def test_is_override_enabled_detects_manual_zone_setpoint(controller_factory, evohome_factory):
    state = evohome_factory.complete_state(zone_mode=ZoneMode.PERMANENT_OVERRIDE)

    assert controller_factory()._is_override_enabled(state.control_system) is True


def test_is_override_enabled_false_when_schedule_followed(controller_factory, evohome_factory):
    state = evohome_factory.complete_state()

    assert controller_factory()._is_override_enabled(state.control_system) is False


def test_is_override_enabled_ignores_zones_with_unusable_data(controller_factory, evohome_factory):
    # a comms-lost zone stuck in an override must not block mode changes forever
    state = evohome_factory.complete_state()
    stuck = evohome_factory.zone(name="stuck", mode=ZoneMode.PERMANENT_OVERRIDE, active_faults=[evohome_factory.fault(FaultType.ZON_A_CL)])
    state.control_system.zones.append(stuck)

    assert controller_factory()._is_override_enabled(state.control_system) is False


async def test_set_mode_skips_when_override_enabled(controller_factory, evohome_factory):
    state = evohome_factory.complete_state(zone_mode=ZoneMode.TEMPORARY_OVERRIDE)

    await controller_factory()._set_mode(SystemMode.AUTO_WITH_ECO, state.location)

    state.control_system.set_mode.assert_not_awaited()


async def test_set_mode_skips_when_mode_already_set(controller_factory, evohome_factory):
    state = evohome_factory.complete_state(system_mode=SystemMode.AUTO_WITH_ECO)

    await controller_factory()._set_mode(SystemMode.AUTO_WITH_ECO, state.location)

    state.control_system.set_mode.assert_not_awaited()


async def test_set_mode_updates_control_system_when_allowed(controller_factory, evohome_factory):
    state = evohome_factory.complete_state(system_mode=SystemMode.AUTO, zone_mode=ZoneMode.FOLLOW_SCHEDULE)

    await controller_factory()._set_mode(SystemMode.AUTO_WITH_ECO, state.location)

    state.control_system.set_mode.assert_awaited_once_with(SystemMode.AUTO_WITH_ECO)


@pytest.mark.parametrize(
    "auto_eco_enabled,schedule_setpoint,outside_temp,expected",
    [
        (False, 20, 30, True),
        (True, 20, None, True),
        (True, 20, 13, True),
        (True, 20, 20, False),
        (True, 20, 17, True),
        # boundary rows: pin the strict inequalities (outside < threshold, outside + diff < highest)
        (True, 16, 14, False),   # outside == outside_temp_threshold
        (True, 20, 18, False),   # outside + inside_temp_diff == highest_setpoint
    ],
)
async def test_is_normal_heating_needed_paths(controller_factory, evohome_factory, settings, auto_eco_enabled, schedule_setpoint, outside_temp, expected):
    state = evohome_factory.complete_state(schedule=evohome_factory.uniform_schedule(setpoint=schedule_setpoint))
    config = replace(settings, auto_eco_enabled=auto_eco_enabled, auto_eco_outside_temp_threshold=14, auto_eco_inside_temp_diff=2)
    controller = controller_factory(config=config, outside_temp=outside_temp)

    with freeze_time("2024-04-10 08:00:00"):
        assert await controller._is_normal_heating_needed(state.location) is expected


def test_get_active_setpoint_picks_most_recent_switchpoint(evohome_factory):
    sp_morning = evohome_factory.switchpoint("07:00:00", 21)
    sp_noon = evohome_factory.switchpoint("12:00:00", 16)
    daily = [evohome_factory.day_schedule(d, [sp_morning, sp_noon]) for d in range(7)]
    state = evohome_factory.complete_state(schedule=daily)

    assert evohome._get_active_setpoint(state.zone, datetime(2024, 4, 10, 11, 0, 0)) == 21.0
    assert evohome._get_active_setpoint(state.zone, datetime(2024, 4, 10, 13, 0, 0)) == 16.0


async def test_is_normal_heating_needed_flips_when_schedule_crosses_switchpoint(controller_factory, evohome_factory, settings):
    """The auto-eco comparison must use the currently active setpoint, not just any."""
    sp_morning = evohome_factory.switchpoint("07:00:00", 21)
    sp_noon = evohome_factory.switchpoint("12:00:00", 16)
    daily = [evohome_factory.day_schedule(d, [sp_morning, sp_noon]) for d in range(7)]
    state = evohome_factory.complete_state(schedule=daily)
    config = replace(settings, auto_eco_enabled=True, auto_eco_outside_temp_threshold=14, auto_eco_inside_temp_diff=2)
    controller = controller_factory(config=config, outside_temp=18)

    with freeze_time("2024-04-10 11:00:00"):  # active setpoint 21: 18 + 2 < 21 -> heat normally
        assert await controller._is_normal_heating_needed(state.location) is True

    with freeze_time("2024-04-10 13:00:00"):  # active setpoint 16: 18 + 2 >= 16 -> eco
        assert await controller._is_normal_heating_needed(state.location) is False


def test_get_highest_set_point_temp_takes_max_across_zones(controller_factory, evohome_factory):
    warm_zone = evohome_factory.zone(name="Living", schedule=evohome_factory.uniform_schedule(setpoint=21))
    cool_zone = evohome_factory.zone(name="Hall", schedule=evohome_factory.uniform_schedule(setpoint=16))
    control_system = evohome_factory.control_system(zones=[cool_zone, warm_zone])
    location = evohome_factory.location(control_systems=[control_system])

    with freeze_time("2024-04-10 08:00:00"):
        assert controller_factory()._get_highest_set_point_temp(location) == 21.0


def test_is_in_preheat_window(controller_factory, evohome_factory, settings):
    state = evohome_factory.complete_state(schedule=evohome_factory.preheat_schedule(20, "11:55:00"))
    controller = controller_factory(config=replace(settings, presence_heating_schedule_grace_time=900))

    with freeze_time("2024-04-07 12:00:00"):
        assert controller.is_in_preheat_window(state.location) is True


def test_is_in_preheat_window_false_outside_window(controller_factory, evohome_factory, settings):
    state = evohome_factory.complete_state(schedule=evohome_factory.preheat_schedule(20, "06:00:00"))
    controller = controller_factory(config=replace(settings, presence_heating_schedule_grace_time=900))

    with freeze_time("2024-04-07 12:00:00"):
        assert controller.is_in_preheat_window(state.location) is False


def test_is_in_preheat_window_false_for_a_flat_schedule(controller_factory, evohome_factory, settings):
    # a single setpoint all week never increases, so it never expects anyone
    state = evohome_factory.complete_state(schedule=evohome_factory.uniform_schedule(20, "11:55:00"))
    controller = controller_factory(config=replace(settings, presence_heating_schedule_grace_time=900))

    with freeze_time("2024-04-07 12:00:00"):
        assert controller.is_in_preheat_window(state.location) is False


def test_is_in_preheat_window_ignores_setpoint_decreases(controller_factory, evohome_factory, settings):
    """A typical day: up at 07:00, down while out at 09:00, up for coming home at 17:00,
    down for the night at 23:00. Only the increases open a pre-heat window."""
    state = evohome_factory.complete_state(
        schedule=evohome_factory.daily_schedule(("07:00:00", 21), ("09:00:00", 18), ("17:00:00", 21), ("23:00:00", 15)),
    )
    controller = controller_factory(config=replace(settings, presence_heating_schedule_grace_time=1800))

    with freeze_time("2024-04-10 07:10:00"):
        assert controller.is_in_preheat_window(state.location) is True
    with freeze_time("2024-04-10 09:10:00"):  # the step down to 18 must not heat an empty house
        assert controller.is_in_preheat_window(state.location) is False
    with freeze_time("2024-04-10 17:10:00"):
        assert controller.is_in_preheat_window(state.location) is True
    with freeze_time("2024-04-10 23:10:00"):
        assert controller.is_in_preheat_window(state.location) is False


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


def test_get_zone_switch_points_accepts_times_without_seconds(evohome_factory):
    state = evohome_factory.complete_state(schedule=evohome_factory.uniform_schedule(20, "07:00"))

    now = datetime(2024, 4, 10, 8, 0, 0)
    switch_points = evohome._get_zone_switch_points(state.zone, now)

    assert switch_points[-1] == (datetime(2024, 4, 10, 7, 0, 0), 20.0)


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


def test_get_highest_set_point_temp_returns_none_when_no_valid_setpoints(controller_factory, evohome_factory):
    state = evohome_factory.complete_state(schedule=[])

    with freeze_time("2024-04-10 08:00:00"):
        assert controller_factory()._get_highest_set_point_temp(state.location) is None


async def test_is_normal_heating_needed_when_no_valid_active_setpoint(controller_factory, evohome_factory, settings):
    state = evohome_factory.complete_state(schedule=[])
    controller = controller_factory(config=replace(settings, auto_eco_enabled=True), outside_temp=25)

    with freeze_time("2024-04-10 08:00:00"):
        assert await controller._is_normal_heating_needed(state.location) is True


def test_get_last_setpoint_increase_survives_a_following_decrease(evohome_factory):
    """Inside a setback that started minutes ago, the last increase is still the one before it."""
    state = evohome_factory.complete_state(schedule=evohome_factory.daily_schedule(("11:55:00", 20), ("12:05:00", 5)))

    now = datetime(2024, 4, 7, 12, 10, 0)  # Sunday, inside the setback
    assert evohome._get_last_setpoint_increase(state.zone, now) == (datetime(2024, 4, 7, 11, 55, 0), 20.0)


def test_get_last_setpoint_increase_wraps_around_the_week(evohome_factory):
    """The earliest switch point of the window is compared with the latest one."""
    # Monday-only heating: Sunday's setback precedes Monday's increase a week later
    monday = evohome_factory.day_schedule(0, [evohome_factory.switchpoint("07:00:00", 21)])
    others = [evohome_factory.day_schedule(d, [evohome_factory.switchpoint("07:00:00", 15)]) for d in range(1, 7)]
    state = evohome_factory.complete_state(schedule=[monday, *others])

    now = datetime(2024, 4, 8, 7, 10, 0)  # Monday 2024-04-08
    assert evohome._get_last_setpoint_increase(state.zone, now) == (datetime(2024, 4, 8, 7, 0, 0), 21.0)


def test_get_last_setpoint_increase_returns_none_for_a_flat_schedule(evohome_factory):
    state = evohome_factory.complete_state(schedule=evohome_factory.uniform_schedule(15, "11:55:00"))

    now = datetime(2024, 4, 7, 12, 10, 0)
    assert evohome._get_last_setpoint_increase(state.zone, now) is None


def test_is_in_preheat_window_stays_open_through_a_following_decrease(controller_factory, evohome_factory, settings):
    state = evohome_factory.complete_state(schedule=evohome_factory.daily_schedule(("11:55:00", 20), ("12:05:00", 5)))
    controller = controller_factory(config=replace(settings, presence_heating_schedule_grace_time=900))

    with freeze_time("2024-04-07 12:09:00"):  # 14 minutes after the increase, within the 15-min window
        assert controller.is_in_preheat_window(state.location) is True


def test_get_zones_filters_zones_with_unusable_data(controller_factory, evohome_factory):
    state = evohome_factory.complete_state(with_fault=False)
    comms_lost = evohome_factory.zone(name="bad", active_faults=[evohome_factory.fault(FaultType.ZON_S_CL)])
    state.control_system.zones.append(comms_lost)

    zones = list(controller_factory().get_zones(state.location))

    assert zones == [state.zone]


def test_get_zones_keeps_zones_with_benign_faults(controller_factory, evohome_factory):
    # a low battery still heats normally, so the zone must keep counting
    state = evohome_factory.complete_state(with_fault=False)
    low_battery = evohome_factory.zone(name="tired", active_faults=[evohome_factory.fault(FaultType.ZON_A_LB)])
    state.control_system.zones.append(low_battery)

    zones = list(controller_factory().get_zones(state.location))

    assert zones == [state.zone, low_battery]


def test_get_zones_keeps_zones_with_unknown_fault_types(controller_factory, evohome_factory):
    # the library passes an unrecognized fault_type through as a plain snake_case str; assume benign
    state = evohome_factory.complete_state(with_fault=False)
    odd = evohome_factory.zone(name="odd", active_faults=[evohome_factory.fault("some_new_fault_type")])
    state.control_system.zones.append(odd)

    zones = list(controller_factory().get_zones(state.location))

    assert zones == [state.zone, odd]


async def test_set_normal_selects_auto_or_eco(controller_factory, evohome_factory, settings):
    config = replace(settings, auto_eco_enabled=True, auto_eco_outside_temp_threshold=14, auto_eco_inside_temp_diff=2)

    with freeze_time("2024-04-10 08:00:00"):
        auto_state = evohome_factory.complete_state(system_mode=SystemMode.AUTO_WITH_ECO, schedule=evohome_factory.uniform_schedule(setpoint=20))
        await controller_factory(config=config, outside_temp=10).set_normal(auto_state.location)
        auto_state.control_system.set_mode.assert_awaited_once_with(SystemMode.AUTO)

        eco_state = evohome_factory.complete_state(system_mode=SystemMode.AUTO, schedule=evohome_factory.uniform_schedule(setpoint=20))
        await controller_factory(config=config, outside_temp=20).set_normal(eco_state.location)
        eco_state.control_system.set_mode.assert_awaited_once_with(SystemMode.AUTO_WITH_ECO)


async def test_set_away_uses_configured_away_mode(controller_factory, evohome_factory, settings):
    state = evohome_factory.complete_state(system_mode=SystemMode.AUTO)

    await controller_factory(config=replace(settings, evohome_away_mode="custom")).set_away(state.location)

    state.control_system.set_mode.assert_awaited_once_with(SystemMode.CUSTOM)


def test_validate_configuration_rejects_unknown_away_mode(controller_factory, settings):
    controller = controller_factory(config=replace(settings, evohome_away_mode="nope"))

    with pytest.raises(ValueError):
        controller.validate_configuration()
