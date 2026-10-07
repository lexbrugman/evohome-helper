import pytest

from dataclasses import replace

from evohome_helper.policy import HeatingMode, Situation, decide


def _situation(**overrides):
    defaults = dict(someone_home=False, someone_home_recently=False, in_preheat_window=False, highest_scheduled_setpoint=20.0, outside_temperature=10.0)
    defaults.update(overrides)
    return Situation(**defaults)


@pytest.mark.parametrize(
    "someone_home,someone_home_recently,in_preheat_window,expected_mode",
    [
        (True, False, False, HeatingMode.NORMAL),
        (False, True, True, HeatingMode.NORMAL),
        (False, False, False, HeatingMode.AWAY),
        # exactly one condition active must still mean away: both conjuncts are load-bearing
        (False, True, False, HeatingMode.AWAY),
        (False, False, True, HeatingMode.AWAY),
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


@pytest.mark.parametrize(
    "auto_eco_enabled,highest_setpoint,outside_temp,expected_mode",
    [
        (False, 20, 30, HeatingMode.NORMAL),
        (True, None, 30, HeatingMode.NORMAL),  # no zone with a usable schedule
        (True, 20, None, HeatingMode.NORMAL),  # outside temperature unknown
        (True, 20, 13, HeatingMode.NORMAL),  # cold outside
        (True, 20, 17, HeatingMode.NORMAL),  # 17 + 2 < 20: zones still need real heat
        (True, 20, 20, HeatingMode.ECO),
        # boundary rows: pin the strict inequalities (outside < threshold, outside + diff < highest)
        (True, 16, 14, HeatingMode.ECO),  # outside == outside_temp_threshold
        (True, 20, 18, HeatingMode.ECO),  # outside + inside_temp_diff == highest setpoint
    ],
)
def test_decide_auto_eco_when_someone_is_home(settings, auto_eco_enabled, highest_setpoint, outside_temp, expected_mode):
    config = replace(settings, auto_eco_enabled=auto_eco_enabled, auto_eco_outside_temp_threshold=14, auto_eco_inside_temp_diff=2)
    situation = _situation(someone_home=True, highest_scheduled_setpoint=highest_setpoint, outside_temperature=outside_temp)

    assert decide(situation, config).mode == expected_mode


def test_decide_applies_auto_eco_to_preheating_too(settings):
    config = replace(settings, auto_eco_enabled=True, auto_eco_outside_temp_threshold=14, auto_eco_inside_temp_diff=2)
    situation = _situation(someone_home=False, someone_home_recently=True, in_preheat_window=True, highest_scheduled_setpoint=20, outside_temperature=20)

    assert decide(situation, config).mode == HeatingMode.ECO


def test_decide_reason_explains_the_eco_choice(settings):
    config = replace(settings, auto_eco_enabled=True, auto_eco_outside_temp_threshold=14, auto_eco_inside_temp_diff=2)

    reason = decide(_situation(someone_home=True, highest_scheduled_setpoint=20, outside_temperature=20), config).reason

    assert reason.startswith("someone is home")
    assert "eco" in reason
