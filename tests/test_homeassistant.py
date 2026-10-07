from dataclasses import replace

from aioresponses import aioresponses

from evohome_helper.homeassistant import HomeAssistantClient


def test_headers_contains_bearer_token(settings):
    client = HomeAssistantClient(settings)
    headers = client._headers()

    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Bearer ")
    assert headers["content-type"] == "application/json"


async def test_get_entity_state_returns_payload(settings):
    client = HomeAssistantClient(settings)
    with aioresponses() as m:
        m.get("http://ha.local/api/states/person.a", payload={
            "state": "home",
            "attributes": {"seconds_since_last_seen": 12},
        })
        result = await client.get_entity_state("person.a")

    await client.close()
    assert result == {"state": "home", "attributes": {"seconds_since_last_seen": 12}}


async def test_get_entity_state_returns_none_on_failure(settings):
    client = HomeAssistantClient(settings)
    with aioresponses() as m:
        m.get("http://ha.local/api/states/person.a", status=500)
        result = await client.get_entity_state("person.a")

    await client.close()
    assert result is None


async def test_get_entity_state_failure_log_does_not_leak_the_token(settings, caplog):
    # the repr of a ClientResponseError embeds the request headers, i.e. the bearer token
    client = HomeAssistantClient(replace(settings, homeassistant_token="s3cret-supervisor-token"))
    with aioresponses() as m:
        m.get("http://ha.local/api/states/person.a", status=404)
        await client.get_entity_state("person.a")

    await client.close()
    assert "failed getting the state of entity 'person.a': ClientResponseError: 404" in caplog.text
    assert "s3cret-supervisor-token" not in caplog.text


async def test_session_is_reused_until_closed(settings):
    client = HomeAssistantClient(settings)

    first = client._get_session()
    second = client._get_session()
    assert first is second

    await client.close()
    third = client._get_session()
    assert third is not first

    await client.close()
