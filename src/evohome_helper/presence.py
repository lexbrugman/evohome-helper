import logging

from dataclasses import dataclass
from datetime import UTC, datetime

from evohome_helper.homeassistant import HomeAssistantClient
from settings import Settings

logger = logging.getLogger(__name__)

# Home Assistant answers for an entity whose integration is down with a normal response
# carrying one of these states; that is the absence of a reading, NOT "not home"
_NO_READING_STATES = {"unavailable", "unknown"}

# Home Assistant reports a presence entity's state as "home", the name of another zone it
# is in, or "not_home" when it is in no zone at all; these are the zones whose entries
# and exits are tracked
_TRACKED_ZONES = ("home",)


@dataclass
class ZonePresence:
    present: bool | None = None  # None until the first reading
    # Home Assistant's last_changed of the reading that flipped `present`, i.e. the moment
    # the entity entered or left the zone. Moving between other zones does not touch
    # them. The first reading after a (re)start cannot observe the transition, so it
    # takes the state's own last_changed as the best estimate.
    entered_at: datetime | None = None
    left_at: datetime | None = None


def _parse_last_changed(entity_state: dict, entity_id: str) -> datetime | None:
    # Home Assistant always includes the moment the state last changed as an aware ISO
    # 8601 timestamp
    try:
        last_changed = datetime.fromisoformat(entity_state["last_changed"])
    except (KeyError, TypeError, ValueError):
        logger.warning("ignoring the unusable last_changed %r of entity '%s'", entity_state.get("last_changed"), entity_id)
        return None

    if last_changed.tzinfo is None:
        last_changed = last_changed.replace(tzinfo=UTC)

    return last_changed


class PresenceTracker:
    def __init__(self, homeassistant: HomeAssistantClient, settings: Settings):
        self._homeassistant = homeassistant
        self._settings = settings
        # entity id -> zone name -> presence record
        self._zones: dict[str, dict[str, ZonePresence]] = {}
        self._unavailable_entities: set[str] = set()

    async def refresh(self) -> None:
        """Read every presence entity once; the queries below answer from that reading."""
        for entity_id in self._settings.homeassistant_presence_entities:
            await self._read(entity_id)

    def is_someone_home(self) -> bool | None:
        # None when no entity has ever been read successfully: unknown, NOT "away" --
        # fabricating an away reading would let a Home Assistant outage turn the heating down
        records = list(self._home_records())
        if not records:
            return None
        return any(home.present for home in records)

    def was_someone_home_recently(self) -> bool:
        """Whether anyone left home within the configured window: a home in daily use
        gets pre-heated for the schedule, a home empty for days (holiday) does not."""
        # computed at query time so that a reading that cannot be refreshed ages out of
        # the window instead of staying in it; UTC because it is compared with Home
        # Assistant's aware timestamps, for which any zone would do
        now = datetime.now(UTC)
        return any(
            home.left_at is not None and (now - home.left_at).total_seconds() <= self._settings.presence_last_home_grace_time
            for home in self._home_records()
        )

    def _home_records(self):
        for entity_id in self._settings.homeassistant_presence_entities:
            records = self._zones.get(entity_id)
            if records is not None:
                yield records["home"]

    async def _read(self, entity_id: str) -> None:
        entity_state = await self._homeassistant.get_entity_state(entity_id)
        if entity_state is None:
            return  # the request failed; keep the last reading

        state = entity_state.get("state")
        if state is None or state in _NO_READING_STATES:
            # keep the last real reading; warn once per outage, not every cycle
            if entity_id not in self._unavailable_entities:
                logger.warning("entity '%s' is %s; using its last known presence", entity_id, state)
                self._unavailable_entities.add(entity_id)
            return

        self._unavailable_entities.discard(entity_id)
        self._record(entity_id, state, _parse_last_changed(entity_state, entity_id))

    def _record(self, entity_id: str, state: str, last_changed: datetime | None) -> None:
        records = self._zones.setdefault(entity_id, {})
        for zone in _TRACKED_ZONES:
            in_zone = state.lower() == zone
            record = records.setdefault(zone, ZonePresence())
            # a flip is an entry or exit (or the first reading); a timestamp that is still
            # unset after an earlier unusable last_changed is backfilled with the next one
            flipped = record.present != in_zone
            record.present = in_zone
            if in_zone and (flipped or record.entered_at is None):
                record.entered_at = last_changed
            elif not in_zone and (flipped or record.left_at is None):
                record.left_at = last_changed
