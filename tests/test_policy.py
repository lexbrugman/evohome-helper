import pytest

from dataclasses import replace

from evohomeasync2 import SystemMode

from evohome_helper.policy import Situation, decide, get_away_mode, validate_configuration


def _situation(**overrides):
    defaults = dict(someone_home=False, someone_home_recently=False, in_preheat_window=False, highest_scheduled_setpoint=20.0, outside_temperature=10.0)
    defaults.update(overrides)
    return Situation(**defaults)


@pytest.mark.parametrize(
    "someone_home,someone_home_recently,in_preheat_window,expected_mode",
    [
        (True, False, False, SystemMode.AUTO),
        (False, True, True, SystemMode.AUTO),
        (False, False, False, SystemMode.AWAY),
        # exactly one condition active must still mean away: both conjuncts are load-bearing
        (False, True, False, SystemMode.AWAY),
        (False, False, True, SystemMode.AWAY),
    ],
)
def test_decide_presence_scenarios(settings, someone_home, someone_home_recently, in_preheat_window, expected_mode):
    situation = _situation(someone_home=someone_home, someone_home_recently=someone_home_recently, in_preheat_window=in_preheat_window)

    assert decide(situation, settings).mode == expected_mode


def test_decide_leaves_the_thermostat_alone_when_presence_is_unknown(settings):
    # even inside a pre-heat window: an unknown reading must never become an away reading
    situation = _situation(someone_home=None, someone_home_recently=True, in_preheat_window=True)

    decision = decide(situation, settings)

    assert decision.mode is None
    assert "unchanged" in decision.reason


def test_decide_uses_the_configured_away_mode(settings):
    config = replace(settings, evohome_away_mode="custom")

    assert decide(_situation(), config).mode == SystemMode.CUSTOM


@pytest.mark.parametrize(
    "auto_eco_enabled,highest_setpoint,outside_temp,expected_mode",
    [
        (False, 20, 30, SystemMode.AUTO),
        (True, None, 30, SystemMode.AUTO),  # no zone with a usable schedule
        (True, 20, None, SystemMode.AUTO),  # outside temperature unknown
        (True, 20, 13, SystemMode.AUTO),  # cold outside
        (True, 20, 17, SystemMode.AUTO),  # 17 + 2 < 20: zones still need real heat
        (True, 20, 20, SystemMode.AUTO_WITH_ECO),
        # boundary rows: pin the strict inequalities (outside < threshold, outside + diff < highest)
        (True, 16, 14, SystemMode.AUTO_WITH_ECO),  # outside == outside_temp_threshold
        (True, 20, 18, SystemMode.AUTO_WITH_ECO),  # outside + inside_temp_diff == highest setpoint
    ],
)
def test_decide_auto_eco_when_someone_is_home(settings, auto_eco_enabled, highest_setpoint, outside_temp, expected_mode):
    config = replace(settings, auto_eco_enabled=auto_eco_enabled, auto_eco_outside_temp_threshold=14, auto_eco_inside_temp_diff=2)
    situation = _situation(someone_home=True, highest_scheduled_setpoint=highest_setpoint, outside_temperature=outside_temp)

    assert decide(situation, config).mode == expected_mode


def test_decide_applies_auto_eco_to_preheating_too(settings):
    config = replace(settings, auto_eco_enabled=True, auto_eco_outside_temp_threshold=14, auto_eco_inside_temp_diff=2)
    situation = _situation(someone_home=False, someone_home_recently=True, in_preheat_window=True, highest_scheduled_setpoint=20, outside_temperature=20)

    assert decide(situation, config).mode == SystemMode.AUTO_WITH_ECO


def test_decide_reason_explains_the_eco_choice(settings):
    config = replace(settings, auto_eco_enabled=True, auto_eco_outside_temp_threshold=14, auto_eco_inside_temp_diff=2)

    reason = decide(_situation(someone_home=True, highest_scheduled_setpoint=20, outside_temperature=20), config).reason

    assert reason.startswith("someone is home")
    assert "eco" in reason


def test_get_away_mode_maps_the_configured_name(settings):
    assert get_away_mode(replace(settings, evohome_away_mode="eco")) == SystemMode.AUTO_WITH_ECO


def test_validate_configuration_rejects_unknown_away_mode(settings):
    with pytest.raises(ValueError):
        validate_configuration(replace(settings, evohome_away_mode="nope"))
