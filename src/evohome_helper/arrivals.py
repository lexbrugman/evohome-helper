import asyncio
import logging

from collections.abc import Callable

from evohome_helper.homeassistant import HomeAssistantClient
from settings import Settings

logger = logging.getLogger(__name__)

_RECONNECT_DELAY_MIN = 5
_RECONNECT_DELAY_MAX = 300


def _is_arrival(variables: dict) -> bool:
    # the trigger should not fire while the state stays "home" (person entities update
    # their attributes with every location fix), but each wake costs a vendor API call,
    # so do not depend on it
    from_state = variables.get("trigger", {}).get("from_state") or {}
    return from_state.get("state") != "home"


class ArrivalListener:
    """Tells the control loop the moment someone arrives home, which polling notices up
    to an interval later.

    Only a signal: the loop still reads presence itself, so a missed or spurious event
    costs nothing but time, and polling carries on while the connection is down."""

    def __init__(self, homeassistant: HomeAssistantClient, settings: Settings):
        self._homeassistant = homeassistant
        self._settings = settings
        self._subscribed = False

    async def run(self, on_arrival: Callable[[], None]) -> None:
        """Listen until cancelled, reconnecting whenever the connection is lost."""
        if not self._settings.homeassistant_presence_entities:
            return

        delay = _RECONNECT_DELAY_MIN
        warned = False

        while True:
            self._subscribed = False
            try:
                await self._listen(on_arrival)
            except Exception as error:
                reason = f"{type(error).__name__}: {error}"

            if self._subscribed:
                # it worked for a while: a new outage, so start over
                delay = _RECONNECT_DELAY_MIN
                warned = False

            # warn once per outage, not on every reconnect attempt
            if not warned:
                logger.warning("not listening for arrivals, falling back to polling until reconnected: %s", reason)
                warned = True

            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_DELAY_MAX)

    async def _listen(self, on_arrival: Callable[[], None]) -> None:
        """Report arrivals until the connection is lost, which raises."""
        trigger = {"platform": "state", "entity_id": self._settings.homeassistant_presence_entities, "to": "home"}

        async with self._homeassistant.subscribe_trigger(trigger) as firings:
            self._subscribed = True
            logger.info("listening for arrivals")

            async for variables in firings:
                if _is_arrival(variables):
                    on_arrival()
