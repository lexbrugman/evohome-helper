from aioresponses import aioresponses

from evohome_helper import homeassistant


def test_headers_contains_bearer_token():
    headers = homeassistant._headers()

    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Bearer ")
    assert headers["content-type"] == "application/json"


async def test_get_entity_state_returns_payload():
    with aioresponses() as m:
        m.get("http://ha.local/api/states/person.a", payload={
            "state": "home",
            "attributes": {"seconds_since_last_seen": 12},
        })
        result = await homeassistant.get_entity_state("person.a")

    assert result == {"state": "home", "attributes": {"seconds_since_last_seen": 12}}


async def test_get_entity_state_returns_none_on_failure():
    with aioresponses() as m:
        m.get("http://ha.local/api/states/person.a", status=500)
        result = await homeassistant.get_entity_state("person.a")

    assert result is None


async def test_session_is_reused_until_closed():
    first = homeassistant._get_session()
    second = homeassistant._get_session()
    assert first is second

    await homeassistant.close()
    third = homeassistant._get_session()
    assert third is not first
