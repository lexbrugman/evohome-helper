import inspect

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import NamedTuple
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

from evohomeasync2 import DayOfWeek, FaultType, SystemMode, ZoneMode
from evohomeasync2.exceptions import InvalidScheduleError, InvalidSystemModeError

from evohome_helper.evohome import EvohomeController
from evohome_helper.evohome_client import EvohomeService
from settings import Settings


# aiohttp 3.14 made ClientResponse.stream_writer a required argument, which aioresponses
# does not yet pass (fixed upstream in pnuckowski/aioresponses#288, not yet released).
# Default it so aioresponses keeps working. The guard makes this a no-op on aiohttp < 3.14,
# and real callers always pass stream_writer, so this never affects production code.
# Remove once aioresponses > 0.7.9 is released.
if "stream_writer" in inspect.signature(aiohttp.ClientResponse.__init__).parameters:
    _orig_client_response_init = aiohttp.ClientResponse.__init__

    def _client_response_init(self, *args, **kwargs):
        kwargs.setdefault("stream_writer", Mock(output_size=0))
        _orig_client_response_init(self, *args, **kwargs)

    aiohttp.ClientResponse.__init__ = _client_response_init


def make_settings(**overrides) -> Settings:
    defaults = dict(
        evohome_location_name="Home",
        evohome_username="user",
        evohome_password="pass",
        evohome_off_temp_threshold=5,
        evohome_away_mode="away",
        evohome_token_cache_path="/nonexistent/evohome_token_cache.json",
        homeassistant_url="http://ha.local",
        homeassistant_token="token",
        homeassistant_presence_entities=["person.a", "person.b"],
        homeassistant_auto_eco_weather_entity="weather.home",
        presence_last_home_grace_time=1200,
        presence_heating_schedule_grace_time=1800,
        auto_eco_enabled=True,
        auto_eco_outside_temp_threshold=14,
        auto_eco_inside_temp_diff=2,
        interval=300,
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture
def settings():
    return make_settings()


# DayOfWeek is Monday-first; the library returns day_of_week as a DayOfWeek enum member
_DAY_ORDER = list(DayOfWeek)


def _make_switchpoint(time_of_day, heat_setpoint):
    return {"heat_setpoint": float(heat_setpoint), "time_of_day": time_of_day}


def _make_day_schedule(day_of_week_int, switchpoints):
    return {"day_of_week": _DAY_ORDER[day_of_week_int], "switchpoints": switchpoints}


def _uniform_schedule(setpoint=20.0, time_of_day="07:00:00"):
    sp = _make_switchpoint(time_of_day, setpoint)
    return [_make_day_schedule(d, [sp]) for d in range(7)]


def _make_fault(fault_type=FaultType.ZON_S_CL):
    # the real library returns active faults as dicts with a fault_type and a since datetime
    return {"fault_type": fault_type, "since": datetime(2024, 4, 1, 12, 0, 0)}


class FakeZone:
    # not a dataclass: the real evohomeasync2.Zone.schedule is a property that RAISES
    # when the zone has no schedule, so the fake models that rather than exposing a plain list
    def __init__(
        self,
        name="zone",
        active_faults=None,
        mode=ZoneMode.FOLLOW_SCHEDULE,
        temperature_status=None,
        setpoint_status=None,
        schedule=None,
    ):
        self.name = name
        self.active_faults = [] if active_faults is None else active_faults
        self.mode = mode
        self.temperature_status = {"is_available": True, "temperature": 19} if temperature_status is None else temperature_status
        self.setpoint_status = {"setpoint_mode": ZoneMode.FOLLOW_SCHEDULE, "target_heat_temperature": 21} if setpoint_status is None else setpoint_status
        self._schedule = _uniform_schedule() if schedule is None else schedule

    @property
    def schedule(self):
        if not self._schedule:
            raise InvalidScheduleError(f"{self.name}: no schedule")
        return self._schedule


@dataclass
class FakeControlSystem:
    mode: SystemMode = SystemMode.AUTO
    zones: list = field(default_factory=list)
    id: str = "system-1"
    # the real library raises for modes the installation does not allow; default to
    # allowing everything so only tests about unsupported modes need to restrict it
    allowed_modes: tuple = tuple(SystemMode)

    def __post_init__(self):
        self.set_mode = AsyncMock(side_effect=self._do_set_mode)
        self.get_schedules = AsyncMock()

    async def _do_set_mode(self, new_mode):
        if new_mode not in self.allowed_modes:
            raise InvalidSystemModeError(f"{self.id}: Unsupported system_mode: {new_mode}")
        self.mode = new_mode


@dataclass
class FakeGateway:
    systems: list = field(default_factory=list)


@dataclass
class FakeLocation:
    name: str = "Home"
    gateways: list = field(default_factory=list)

    def now(self):
        # the real Location.now() returns an aware datetime, in the location's own timezone
        return datetime.now(UTC)

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
    def control_system(mode=SystemMode.AUTO, zones=None, system_id="system-1", allowed_modes=tuple(SystemMode)):
        return FakeControlSystem(mode=mode, zones=zones or [], id=system_id, allowed_modes=allowed_modes)

    @staticmethod
    def fault(fault_type=FaultType.ZON_S_CL):
        return _make_fault(fault_type)

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
            temperature_status={"is_available": True, "temperature": 20},
            active_faults=[_make_fault()] if with_fault else [],
            schedule=schedule if schedule is not None else EvohomeFactory.uniform_schedule(setpoint=float(setpoint)),
        )
        control = EvohomeFactory.control_system(mode=system_mode, zones=[zone])
        location = EvohomeFactory.location(name=location_name, control_systems=[control])
        return State(location=location, control_system=control, zone=zone)


@pytest.fixture
def evohome_factory():
    return EvohomeFactory


class FakeHomeAssistant:
    """Faithful double of HomeAssistantClient: get_entity_state returns a dict, or None
    when the request itself failed (an outage, a bad token, a wrong entity id).

    NOTE: an entity whose integration is down is NOT a failed request -- HA returns a
    normal response with state "unavailable"/"unknown"; model that as a state dict."""

    def __init__(self):
        self._states = {}

    def set_state(self, entity_id, state):
        self._states[entity_id] = state

    async def get_entity_state(self, entity_id):
        return self._states.get(entity_id)

    async def close(self):
        pass


@pytest.fixture
def fake_homeassistant():
    return FakeHomeAssistant()


@pytest.fixture
def make_service(settings):
    """Build an EvohomeService, optionally with its cached client pre-installed."""

    def _build(config=None, locations=None, client=None):
        service = EvohomeService(config or settings)
        if client is not None:
            service._client = client
        elif locations is not None:
            service._client = FakeEvohomeClient(locations=locations)
        return service

    return _build


@pytest.fixture
def controller_factory(settings):
    """Build an EvohomeController wired to a real EvohomeService (thin passthrough to the
    fake control systems) and a stubbed weather service returning `outside_temp`."""

    def _build(config=None, outside_temp=None):
        config = config or settings
        service = EvohomeService(config)
        weather = Mock()
        weather.get_current_temperature = AsyncMock(return_value=outside_temp)
        return EvohomeController(service, weather, config)

    return _build
