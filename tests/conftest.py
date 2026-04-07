import sys
import types
from dataclasses import dataclass, field
from datetime import datetime
from typing import NamedTuple
from unittest.mock import AsyncMock

import pytest

from evohomeasync2.schemas import SystemMode, ZoneMode


if "settings" not in sys.modules:
    settings = types.SimpleNamespace(
        EVOHOME_USERNAME="user",
        EVOHOME_PASSWORD="pass",
        EVOHOME_LOCATION_NAME="Home",
        EVOHOME_OFF_TEMP_THRESHOLD=5,
        EVOHOME_AWAY_MODE="away",
        AUTO_ECO_ENABLED=True,
        AUTO_ECO_OUTSIDE_TEMP_THRESHOLD=14,
        AUTO_ECO_INSIDE_TEMP_DIFF=2,
        PRESENCE_HEATING_SCHEDULE_GRACE_TIME=1800,
        PRESENCE_LAST_HOME_GRACE_TIME=1200,
        HOMEASSISTANT_URL="http://ha.local",
        HOMEASSISTANT_TOKEN="token",
        HOMEASSISTANT_AUTO_ECO_WEATHER_ENTITY="weather.home",
        HOMEASSISTANT_PRESENCE_ENTITIES=["person.a", "person.b"],
        INTERVAL=300,
    )
    sys.modules["settings"] = settings


_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _make_switchpoint(time_of_day, heat_setpoint):
    return {"heat_setpoint": float(heat_setpoint), "time_of_day": time_of_day}


def _make_day_schedule(day_of_week_int, switchpoints):
    return {"day_of_week": _DAY_NAMES[day_of_week_int], "switchpoints": switchpoints}


def _uniform_schedule(setpoint=20.0, time_of_day="07:00:00"):
    sp = _make_switchpoint(time_of_day, setpoint)
    return [_make_day_schedule(d, [sp]) for d in range(7)]


@dataclass
class FakeZone:
    name: str = "zone"
    active_faults: list = field(default_factory=list)
    mode: ZoneMode = ZoneMode.FOLLOW_SCHEDULE
    temperature_status: dict = field(default_factory=lambda: {"temperature": 19})
    setpoint_status: dict = field(default_factory=lambda: {"setpoint_mode": "FollowSchedule", "target_heat_temperature": 21})
    schedule: list = field(default_factory=_uniform_schedule)


@dataclass
class FakeControlSystem:
    mode: SystemMode = SystemMode.AUTO
    zones: list = field(default_factory=list)
    id: str = "system-1"

    def __post_init__(self):
        self.set_mode = AsyncMock(side_effect=self._do_set_mode)

    async def _do_set_mode(self, new_mode):
        self.mode = new_mode

    async def get_schedules(self):
        pass


@dataclass
class FakeGateway:
    systems: list = field(default_factory=list)


@dataclass
class FakeLocation:
    name: str = "Home"
    gateways: list = field(default_factory=list)

    def now(self):
        return datetime.now()

    async def update(self):
        pass


@dataclass
class FakeEvohomeClient:
    locations: list = field(default_factory=list)

    async def update(self, dont_update_status=False):
        pass


class State(NamedTuple):
    location: FakeLocation
    control_system: FakeControlSystem
    zone: FakeZone


class EvohomeFactory:
    @staticmethod
    def zone(**kwargs):
        return FakeZone(**kwargs)

    @staticmethod
    def control_system(mode=SystemMode.AUTO, zones=None, system_id="system-1"):
        return FakeControlSystem(mode=mode, zones=zones or [], id=system_id)

    @staticmethod
    def location(*, name="Home", control_systems=None):
        gateway = FakeGateway(systems=control_systems or [])
        return FakeLocation(name=name, gateways=[gateway])

    @staticmethod
    def switchpoint(time_of_day, heat_setpoint):
        return _make_switchpoint(time_of_day, heat_setpoint)

    @staticmethod
    def day_schedule(day_of_week_int, switchpoints):
        return _make_day_schedule(day_of_week_int, switchpoints)

    @staticmethod
    def uniform_schedule(setpoint=20.0, time_of_day="07:00:00"):
        return _uniform_schedule(setpoint, time_of_day)

    @staticmethod
    def complete_state(*, location_name="Home", zone_mode=ZoneMode.FOLLOW_SCHEDULE, system_mode=SystemMode.AUTO, with_fault=False, setpoint=21, schedule=None):
        zone = EvohomeFactory.zone(
            name="Living",
            mode=zone_mode,
            setpoint_status={"setpoint_mode": zone_mode, "target_heat_temperature": setpoint},
            temperature_status={"temperature": 20},
            active_faults=["fault"] if with_fault else [],
            schedule=schedule if schedule is not None else EvohomeFactory.uniform_schedule(setpoint=float(setpoint)),
        )
        control = EvohomeFactory.control_system(mode=system_mode, zones=[zone])
        location = EvohomeFactory.location(name=location_name, control_systems=[control])
        return State(location=location, control_system=control, zone=zone)


@pytest.fixture
def evohome_factory():
    return EvohomeFactory


@pytest.fixture
def evohome_client():
    from evohome_helper import evohome

    def _build(*, location_name="Home", **state_kwargs):
        state = EvohomeFactory.complete_state(location_name=location_name, **state_kwargs)
        client = FakeEvohomeClient(locations=[state.location])
        evohome._evohome_client = client
        return state

    return _build


@pytest.fixture(autouse=True)
def reset_state():
    from evohome_helper import presence, evohome

    presence.last_known_presence_state.clear()
    evohome._evohome_client = None


