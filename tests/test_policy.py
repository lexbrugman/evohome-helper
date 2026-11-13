import pytest

from dataclasses import replace

from evohome_helper.policy import HeatingMode, Situation, decide


def _situation(**overrides):
    defaults = dict(
        someone_home=False,
        seconds_since_last_departure=None,
        seconds_since_scheduled_increase=None,
        highest_scheduled_setpoint=20.0,
        outside_temperature=10.0,
    )
    defaults.update(overrides)
    return Situation(**defaults)


# the windows of the settings fixture
DAILY_USE = 86400
PREHEAT = 1800
AWAY_DELAY = 300


@pytest.mark.parametrize(
    "someone_home,since_departure,since_increase,expected_mode",
    [
        (True, None, None, HeatingMode.NORMAL),
        (False, 3600, 600, HeatingMode.NORMAL),  # in daily use, and the schedule just raised a setpoint
        (False, None, None, HeatingMode.AWAY),
        # exactly one condition active must still mean away: both conjuncts are load-bearing
        (False, 3600, 3600, HeatingMode.AWAY),  # the last increase was too long ago
        (False, 3600, None, HeatingMode.AWAY),  # no schedule ever raises a setpoint
        (False, 7 * 86400, 600, HeatingMode.AWAY),  # empty for a week: a holiday
        (False, None, 600, HeatingMode.AWAY),  # no departure is known
        # boundary rows: a window ends at exactly its length
        (False, DAILY_USE - 1, PREHEAT - 1, HeatingMode.NORMAL),
        (False, DAILY_USE, PREHEAT - 1, HeatingMode.AWAY),
        (False, DAILY_USE - 1, PREHEAT, HeatingMode.AWAY),
    ],
)
def test_decide_presence_scenarios(settings, someone_home, since_departure, since_increase, expected_mode):
    situation = _situation(someone_home=someone_home, seconds_since_last_departure=since_departure, seconds_since_scheduled_increase=since_increase)

    assert decide(situation, settings).mode == expected_mode


def test_decide_leaves_the_thermostat_alone_when_presence_is_unknown(settings):
    # even inside a pre-heat window: an unknown reading must never become an away reading
    situation = _situation(someone_home=None, seconds_since_last_departure=3600, seconds_since_scheduled_increase=600)

    decision = decide(situation, settings)

    assert decision.mode is None
    assert "unchanged" in decision.reason


def test_decide_leaves_the_thermostat_alone_when_someone_left_just_now(settings):
    # unchanged, not normal: an away mode that is already set must not be undone
    decision = decide(_situation(seconds_since_last_departure=AWAY_DELAY - 1), settings)

    assert decision.mode is None
    assert "unchanged" in decision.reason


@pytest.mark.parametrize(
    "away_delay,since_departure",
    [
        (AWAY_DELAY, AWAY_DELAY),  # the delay has passed
        (AWAY_DELAY, None),  # an unknown departure time: never assume someone is still around
        (0, 0),  # a delay of zero disables it
    ],
)
def test_decide_switches_to_away_without_a_delay_to_wait_out(settings, away_delay, since_departure):
    config = replace(settings, presence_away_delay=away_delay)

    assert decide(_situation(seconds_since_last_departure=since_departure), config).mode == HeatingMode.AWAY


@pytest.mark.parametrize(
    "someone_home,since_increase",
    [
        (True, None),  # the delay only ever holds back the switch to away
        (False, 600),
    ],
)
def test_decide_heats_normally_despite_a_recent_departure(settings, someone_home, since_increase):
    situation = _situation(someone_home=someone_home, seconds_since_last_departure=10, seconds_since_scheduled_increase=since_increase)

    assert decide(situation, settings).mode == HeatingMode.NORMAL


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
    situation = _situation(seconds_since_last_departure=3600, seconds_since_scheduled_increase=600, highest_scheduled_setpoint=20, outside_temperature=20)

    assert decide(situation, config).mode == HeatingMode.ECO


def test_decide_reason_explains_the_eco_choice(settings):
    config = replace(settings, auto_eco_enabled=True, auto_eco_outside_temp_threshold=14, auto_eco_inside_temp_diff=2)

    reason = decide(_situation(someone_home=True, highest_scheduled_setpoint=20, outside_temperature=20), config).reason

    assert reason.startswith("someone is home")
    assert "eco" in reason
