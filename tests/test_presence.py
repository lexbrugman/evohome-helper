from datetime import UTC, datetime, timedelta

from freezegun import freeze_time

from evohome_helper.presence import PresenceTracker, ZonePresence

NOW = datetime(2024, 4, 10, 8, 0, 0, tzinfo=UTC)


def _ago(seconds):
    return NOW - timedelta(seconds=seconds)


def _state(state, *, seconds_ago=100, last_changed=None):
    # shaped like a real /api/states/<entity_id> response
    if last_changed is None:
        last_changed = _ago(seconds_ago).isoformat()
    return {"state": state, "attributes": {}, "last_changed": last_changed}


def _home(tracker, entity_id):
    return tracker._zones[entity_id]["home"]


async def test_refresh_records_the_reading(fake_homeassistant, settings):
    fake_homeassistant.set_state("person.a", _state("home", seconds_ago=12))
    tracker = PresenceTracker(fake_homeassistant, settings)

    await tracker.refresh()

    assert _home(tracker, "person.a") == ZonePresence(present=True, entered_at=_ago(12))
    assert "person.b" not in tracker._zones  # never answered


async def test_first_reading_of_an_away_entity_estimates_the_departure(fake_homeassistant, settings):
    # no transition can be observed on the first reading, so last_changed is the best estimate
    fake_homeassistant.set_state("person.a", _state("not_home", seconds_ago=500))
    tracker = PresenceTracker(fake_homeassistant, settings)

    await tracker.refresh()

    assert _home(tracker, "person.a") == ZonePresence(present=False, left_at=_ago(500))


async def test_leaving_and_returning_home_are_recorded(fake_homeassistant, settings):
    tracker = PresenceTracker(fake_homeassistant, settings)

    fake_homeassistant.set_state("person.a", _state("home", seconds_ago=3000))
    await tracker.refresh()
    fake_homeassistant.set_state("person.a", _state("not_home", seconds_ago=2000))
    await tracker.refresh()
    assert _home(tracker, "person.a") == ZonePresence(present=False, entered_at=_ago(3000), left_at=_ago(2000))

    fake_homeassistant.set_state("person.a", _state("home", seconds_ago=100))
    await tracker.refresh()
    assert _home(tracker, "person.a") == ZonePresence(present=True, entered_at=_ago(100), left_at=_ago(2000))


async def test_moving_between_other_zones_does_not_count_as_leaving_home(fake_homeassistant, settings):
    tracker = PresenceTracker(fake_homeassistant, settings)

    fake_homeassistant.set_state("person.a", _state("home", seconds_ago=3000))
    await tracker.refresh()
    fake_homeassistant.set_state("person.a", _state("Work", seconds_ago=2000))
    await tracker.refresh()
    fake_homeassistant.set_state("person.a", _state("Gym", seconds_ago=100))
    await tracker.refresh()

    assert _home(tracker, "person.a").left_at == _ago(2000)
    with freeze_time(NOW):
        assert tracker.seconds_since_last_departure() == 2000


async def test_request_failure_keeps_the_last_reading(fake_homeassistant, settings):
    fake_homeassistant.set_state("person.a", _state("home", seconds_ago=12))
    tracker = PresenceTracker(fake_homeassistant, settings)
    await tracker.refresh()

    fake_homeassistant.set_state("person.a", None)  # the request itself fails
    await tracker.refresh()

    assert _home(tracker, "person.a") == ZonePresence(present=True, entered_at=_ago(12))
    assert tracker.is_someone_home() is True


async def test_nothing_is_known_before_a_successful_reading(fake_homeassistant, settings):
    tracker = PresenceTracker(fake_homeassistant, settings)

    await tracker.refresh()

    assert tracker.is_someone_home() is None
    assert tracker.seconds_since_last_departure() is None


async def test_unavailable_entity_is_not_a_reading(fake_homeassistant, settings):
    # an integration that is down must not be mistaken for "not home"
    fake_homeassistant.set_state("person.a", _state("unavailable"))
    fake_homeassistant.set_state("person.b", _state("unknown"))
    tracker = PresenceTracker(fake_homeassistant, settings)

    await tracker.refresh()

    assert tracker.is_someone_home() is None


async def test_unavailable_entity_keeps_last_known_presence(fake_homeassistant, settings, caplog):
    fake_homeassistant.set_state("person.a", _state("home"))
    tracker = PresenceTracker(fake_homeassistant, settings)
    await tracker.refresh()
    assert tracker.is_someone_home() is True

    fake_homeassistant.set_state("person.a", _state("unavailable"))
    await tracker.refresh()
    assert tracker.is_someone_home() is True
    await tracker.refresh()

    # warned once for the outage, not once per cycle
    assert sum("person.a" in record.message and "unavailable" in record.message for record in caplog.records) == 1

    fake_homeassistant.set_state("person.a", _state("not_home"))
    await tracker.refresh()
    assert tracker.is_someone_home() is False


@freeze_time(NOW)
async def test_someone_home_while_another_left(fake_homeassistant, settings):
    fake_homeassistant.set_state("person.a", _state("not_home", seconds_ago=100))
    fake_homeassistant.set_state("person.b", _state("home", seconds_ago=9999))
    tracker = PresenceTracker(fake_homeassistant, settings)

    await tracker.refresh()

    assert tracker.is_someone_home() is True
    assert tracker.seconds_since_last_departure() == 100


@freeze_time(NOW)
async def test_seconds_since_last_departure_takes_the_most_recent_one(fake_homeassistant, settings):
    fake_homeassistant.set_state("person.a", _state("not_home", seconds_ago=9999))
    fake_homeassistant.set_state("person.b", _state("Work", seconds_ago=1201))
    tracker = PresenceTracker(fake_homeassistant, settings)

    await tracker.refresh()

    assert tracker.seconds_since_last_departure() == 1201


@freeze_time(NOW)
async def test_unusable_last_changed_is_not_a_known_departure(fake_homeassistant, settings):
    fake_homeassistant.set_state("person.a", _state("not_home", last_changed="garbage"))
    fake_homeassistant.set_state("person.b", {"state": "not_home", "attributes": {}})
    tracker = PresenceTracker(fake_homeassistant, settings)

    await tracker.refresh()

    assert tracker.seconds_since_last_departure() is None
    # the entities are still valid presence readings
    assert tracker.is_someone_home() is False


async def test_unusable_last_changed_is_backfilled_by_the_next_reading(fake_homeassistant, settings):
    tracker = PresenceTracker(fake_homeassistant, settings)
    fake_homeassistant.set_state("person.a", _state("not_home", last_changed="garbage"))
    await tracker.refresh()
    assert _home(tracker, "person.a") == ZonePresence(present=False, left_at=None)

    fake_homeassistant.set_state("person.a", _state("Work", seconds_ago=100))
    await tracker.refresh()
    assert _home(tracker, "person.a") == ZonePresence(present=False, left_at=_ago(100))

    # but a recorded departure is not overwritten by later moves between away zones
    fake_homeassistant.set_state("person.a", _state("Gym", seconds_ago=10))
    await tracker.refresh()
    assert _home(tracker, "person.a").left_at == _ago(100)


@freeze_time(NOW)
async def test_a_brief_return_home_makes_for_a_new_departure(fake_homeassistant, settings):
    tracker = PresenceTracker(fake_homeassistant, settings)
    for state, seconds_ago in (("not_home", 2000), ("home", 100), ("not_home", 10)):
        fake_homeassistant.set_state("person.a", _state(state, seconds_ago=seconds_ago))
        await tracker.refresh()

    assert tracker.seconds_since_last_departure() == 10


async def test_a_reading_that_cannot_be_refreshed_keeps_ageing(fake_homeassistant, settings):
    """A departure must not stay recent for as long as HA is unreachable."""
    tracker = PresenceTracker(fake_homeassistant, settings)
    fake_homeassistant.set_state("person.a", _state("not_home", seconds_ago=100))
    fake_homeassistant.set_state("person.b", _state("not_home", seconds_ago=9999))

    with freeze_time(NOW):
        await tracker.refresh()
        assert tracker.seconds_since_last_departure() == 100

    fake_homeassistant.set_state("person.a", None)  # HA goes down
    fake_homeassistant.set_state("person.b", None)

    with freeze_time(NOW + timedelta(seconds=1200)):
        await tracker.refresh()
        assert tracker.seconds_since_last_departure() == 1300
