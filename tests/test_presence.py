from evohome_helper.presence import PresenceTracker


async def test_get_data_returns_parsed_response(fake_homeassistant, settings):
    fake_homeassistant.set_state("person.a", {"state": "home", "attributes": {"seconds_since_last_seen": 12}})
    tracker = PresenceTracker(fake_homeassistant, settings)

    result = await tracker._get_data("person.a")

    assert result == {"is_someone_home": True, "seconds_since_last_seen": 12}


async def test_get_data_uses_last_known_state_on_request_failure(fake_homeassistant, settings):
    fake_homeassistant.set_state("person.a", {"state": "home", "attributes": {"seconds_since_last_seen": 12}})
    tracker = PresenceTracker(fake_homeassistant, settings)

    await tracker._get_data("person.a")

    fake_homeassistant.set_state("person.a", None)  # entity becomes unavailable
    result = await tracker._get_data("person.a")

    assert result == {"is_someone_home": True, "seconds_since_last_seen": 12}


async def test_get_data_returns_none_when_entity_never_seen(fake_homeassistant, settings):
    tracker = PresenceTracker(fake_homeassistant, settings)

    result = await tracker._get_data("person.unknown")

    assert result is None


async def test_is_presence_known_reflects_available_data(fake_homeassistant, settings):
    tracker = PresenceTracker(fake_homeassistant, settings)
    assert tracker.is_presence_known() is False

    fake_homeassistant.set_state("person.a", {"state": "home", "attributes": {"seconds_since_last_seen": 5}})
    await tracker._get_data("person.a")

    assert tracker.is_presence_known() is True


async def test_is_someone_home_and_away_grace_period(fake_homeassistant, settings):
    fake_homeassistant.set_state("person.a", {"state": "not_home", "attributes": {"seconds_since_last_seen": 100}})
    fake_homeassistant.set_state("person.b", {"state": "home", "attributes": {"seconds_since_last_seen": 9999}})
    tracker = PresenceTracker(fake_homeassistant, settings)

    assert await tracker.is_someone_home() is True
    assert await tracker.is_in_away_grace_period() is True


async def test_is_in_away_grace_period_false_when_all_expired(fake_homeassistant, settings):
    fake_homeassistant.set_state("person.a", {"state": "not_home", "attributes": {"seconds_since_last_seen": 9999}})
    fake_homeassistant.set_state("person.b", {"state": "not_home", "attributes": {"seconds_since_last_seen": None}})
    tracker = PresenceTracker(fake_homeassistant, settings)

    assert await tracker.is_in_away_grace_period() is False


async def test_is_in_away_grace_period_true_when_last_seen_is_zero(fake_homeassistant, settings):
    fake_homeassistant.set_state("person.a", {"state": "not_home", "attributes": {"seconds_since_last_seen": 0}})
    fake_homeassistant.set_state("person.b", {"state": "not_home", "attributes": {"seconds_since_last_seen": 9999}})
    tracker = PresenceTracker(fake_homeassistant, settings)

    assert await tracker.is_in_away_grace_period() is True


async def test_is_in_away_grace_period_none_does_not_count_toward_grace(fake_homeassistant, settings):
    fake_homeassistant.set_state("person.a", {"state": "not_home", "attributes": {"seconds_since_last_seen": None}})
    fake_homeassistant.set_state("person.b", {"state": "not_home", "attributes": {"seconds_since_last_seen": 9999}})
    tracker = PresenceTracker(fake_homeassistant, settings)

    assert await tracker.is_in_away_grace_period() is False
