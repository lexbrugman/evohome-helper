import sys
import types
from dataclasses import dataclass, field
from unittest.mock import Mock

import pytest


def _switchpoint(time_of_day, heat_setpoint):
    return {"TimeOfDay": time_of_day, "heatSetpoint": heat_setpoint}


def _day_schedule(day_of_week, switchpoints):
    return {"DayOfWeek": day_of_week, "Switchpoints": switchpoints}


def _switch_point_reference(day_of_week, time_of_day):
    return {"DayOfWeek": day_of_week, "TimeOfDay": time_of_day}


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
    )
    sys.modules["settings"] = settings


if "evohomeclient2" not in sys.modules:
    class DummyBaseClient:
        def __init__(self, *_args, **_kwargs):
            self.locations = []

        def installation(self):
            return None

    sys.modules["evohomeclient2"] = types.SimpleNamespace(EvohomeClient=DummyBaseClient)


@dataclass
class FakeZone:
    name: str = "zone"
    activeFaults: list = field(default_factory=list)
    setpointStatus: dict = field(default_factory=lambda: {"setpointMode": "FollowSchedule", "targetHeatTemperature": 20})
    temperatureStatus: dict = field(default_factory=lambda: {"temperature": 19})
    _schedule: dict = field(default_factory=lambda: {"DailySchedules": []})

    def schedule(self):
        return self._schedule

    def set_schedule(self, daily_schedules):
        self._schedule = {"DailySchedules": daily_schedules}

    def set_weekly_schedule(self, setpoint=20, time_of_day="07:00:00"):
        self._schedule = {
            "DailySchedules": [
                _day_schedule(day, [_switchpoint(time_of_day, setpoint)])
                for day in range(7)
            ]
        }


@dataclass
class FakeControlSystem:
    mode: str = "Auto"
    zones: dict = field(default_factory=dict)
    systemId: str = "system-1"
    def __post_init__(self):
        self.systemModeStatus = {"mode": self.mode}
        self.set_status = Mock(side_effect=self._set_status)

    def _set_status(self, status):
        from evohome_helper.evohome import ThermostatStatus

        next_status = ThermostatStatus.get_by_status(status)
        if next_status is not None:
            self.systemModeStatus["mode"] = next_status.mode


@dataclass
class FakeGateway:
    control_systems: dict


@dataclass
class FakeLocation:
    name: str = "Home"
    gateways: dict = field(default_factory=dict)


@dataclass
class FakeEvohomeClient:
    locations: list = field(default_factory=list)
    installation_calls: int = 0

    def installation(self):
        self.installation_calls += 1

    def get_location(self, name):
        self.installation()
        for location in self.locations:
            if location.name == name:
                return location
        return None


@pytest.fixture
def evohome_factory():
    class Factory:
        @staticmethod
        def weekly_schedule(setpoint=20, time_of_day="07:00:00"):
            return {
                "DailySchedules": [
                    Factory.day_schedule(day, [Factory.switchpoint(time_of_day, setpoint)])
                    for day in range(7)
                ]
            }

        @staticmethod
        def day_schedule(day_of_week, switchpoints):
            return _day_schedule(day_of_week, switchpoints)

        @staticmethod
        def switchpoint(time_of_day, heat_setpoint):
            return _switchpoint(time_of_day, heat_setpoint)

        @staticmethod
        def switch_point_reference(day_of_week, time_of_day):
            return _switch_point_reference(day_of_week, time_of_day)

        @staticmethod
        def zone(**kwargs):
            return FakeZone(**kwargs)

        @staticmethod
        def control_system(mode="Auto", zones=None, system_id="system-1"):
            return FakeControlSystem(mode=mode, zones=zones or {}, systemId=system_id)

        @staticmethod
        def location(*, name="Home", control_systems=None, gateway_id="gateway-1"):
            return FakeLocation(name=name, gateways={gateway_id: FakeGateway(control_systems=control_systems or {})})

        @staticmethod
        def complete_state(*, location_name="Home", zone_mode="FollowSchedule", system_mode="Auto", with_fault=False, setpoint=21):
            zone = FakeZone(
                name="Living",
                setpointStatus={"setpointMode": zone_mode, "targetHeatTemperature": setpoint},
                temperatureStatus={"temperature": 20},
                activeFaults=["fault"] if with_fault else [],
            )
            control = FakeControlSystem(mode=system_mode, zones={"z1": zone}, systemId="sys-1")
            location = FakeLocation(name=location_name, gateways={"g1": FakeGateway(control_systems={"c1": control})})
            return {"location": location, "control_system": control, "zone": zone}

    return Factory


@pytest.fixture
def evohome_client(evohome_factory):
    from evohome_helper import evohome

    def _build(*, location_name="Home", **state_kwargs):
        state = evohome_factory.complete_state(location_name=location_name, **state_kwargs)
        client = FakeEvohomeClient(locations=[state["location"]])
        evohome._evohome_client = client
        return client, state

    yield _build
    evohome._evohome_client = None


@pytest.fixture
def patch_evohome_client_class(monkeypatch):
    from evohome_helper import evohome

    def _apply(client_class):
        monkeypatch.setattr("evohome_helper.evohome.EvohomeClient", client_class)
        monkeypatch.setattr("evohome_helper.evohome.sleep", lambda _seconds: None)
        evohome._evohome_client = None

    yield _apply
    evohome._evohome_client = None


@pytest.fixture(autouse=True)
def reset_cached_state():
    from evohome_helper import presence, weather

    presence.last_known_presence_state.clear()

    presence._get_data.cache_clear()
    weather.get_temperature.cache_clear()


