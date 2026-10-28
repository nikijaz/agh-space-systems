from __future__ import annotations

import threading
import time
from collections import deque
from enum import Enum
from typing import Callable, Literal, TypedDict, Union

SERVOS = {"fuel_intake": 0, "oxidizer_intake": 1, "fuel_main": 2, "oxidizer_main": 3}
RELAYS = {"oxidizer_heater": 0, "igniter": 1, "parachute": 2}
SENSORS = {"fuel_level": 0, "oxidizer_level": 1, "altitude": 2, "oxidizer_pressure": 3, "angle": 4}
SENSOR_NAMES = {device_id: name for name, device_id in SENSORS.items()}
SERVO_NAMES = {device_id: name for name, device_id in SERVOS.items()}

INTAKES = ("fuel_intake", "oxidizer_intake")
MAINS = ("fuel_main", "oxidizer_main")
LEVELS = {"fuel_intake": "fuel_level", "oxidizer_intake": "oxidizer_level"}

TANK_FULL = 99.0
PRESSURE_MIN = 40.0
PRESSURE_MAX = 65.0
PRESSURE_LIMIT = 80.0
THRUST_MIN = 55.0
THRUST_MAX = 65.0
CHUTE_MAX_SPEED = 30.0
OPEN_POSITION = 30
GROUND = 0.2

EventLevel = Literal["info", "warn", "error"]
ActuatorKind = Literal["servo", "relay"]
ActuatorValue = Union[int, bool]


class Phase(Enum):
    IDLE = "IDLE"
    FILLING_OX = "FILLING_OX"
    FILLING_FUEL = "FILLING_FUEL"
    HEATING = "HEATING"
    READY = "READY"
    IGNITION = "IGNITION"
    FLIGHT = "FLIGHT"
    COAST = "COAST"
    APOGEE = "APOGEE"
    DESCENT = "DESCENT"
    LANDED = "LANDED"
    FAILED = "FAILED"


FLIGHT_PHASES = frozenset({Phase.IGNITION, Phase.FLIGHT, Phase.COAST, Phase.APOGEE, Phase.DESCENT})
LOADED_PHASES = frozenset({Phase.HEATING, Phase.READY}) | FLIGHT_PHASES


SensorValues = dict[str, float]
ServoPositions = dict[str, int]
RelayStates = dict[str, bool]


class MissionSnapshot(TypedDict):
    phase: Phase
    sensors: SensorValues
    servos: ServoPositions
    relays: RelayStates
    velocity: float
    apogee: float
    apogee_reached: bool
    link_up: bool
    events: list[tuple[str, EventLevel, str]]


class Denied(Exception):
    pass


class CommandError(Exception):
    pass


class MissionState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._phase = Phase.IDLE
        self._sensors: SensorValues = {
            "fuel_level": 0.0,
            "oxidizer_level": 0.0,
            "altitude": 0.0,
            "oxidizer_pressure": 0.0,
            "angle": 0.0,
        }
        self._servos: ServoPositions = {
            "fuel_intake": 100,
            "oxidizer_intake": 100,
            "fuel_main": 100,
            "oxidizer_main": 100,
        }
        self._relays: RelayStates = {"oxidizer_heater": False, "igniter": False, "parachute": False}
        self._velocity = 0.0
        self._apogee = 0.0
        self._apogee_reached = False
        self._link_up = True
        self._events: deque[tuple[str, EventLevel, str]] = deque(maxlen=12)
        self._last_altitude: float | None = None
        self._last_time: float | None = None

    def log(self, level: EventLevel, text: str) -> None:
        with self._lock:
            self._append_event(level, text)

    def update_sensor(self, name: str, value: float) -> None:
        with self._lock:
            self._sensors[name] = value
            if name != "altitude":
                return
            now = time.monotonic()
            if self._last_altitude is not None and self._last_time is not None and now > self._last_time:
                raw_velocity = (value - self._last_altitude) / (now - self._last_time)
                self._velocity = self._velocity * 0.5 + raw_velocity * 0.5
            self._last_altitude = value
            self._last_time = now
            self._apogee = max(self._apogee, value)

    def update_servo(self, name: str, position: int) -> None:
        with self._lock:
            self._servos[name] = position

    def set_relay(self, name: str, on: bool) -> None:
        with self._lock:
            self._relays[name] = on

    def set_phase(self, phase: Phase) -> None:
        with self._lock:
            self._phase = phase

    def set_link(self, is_up: bool) -> None:
        with self._lock:
            self._link_up = is_up

    def snapshot(self) -> MissionSnapshot:
        with self._lock:
            return {
                "phase": self._phase,
                "sensors": self._sensors.copy(),
                "servos": self._servos.copy(),
                "relays": self._relays.copy(),
                "velocity": self._velocity,
                "apogee": self._apogee,
                "apogee_reached": self._apogee_reached,
                "link_up": self._link_up,
                "events": list(self._events),
            }

    def evaluate(self) -> None:
        with self._lock:
            pressure = self._sensors["oxidizer_pressure"]
            if self._phase == Phase.HEATING and THRUST_MIN <= pressure <= THRUST_MAX:
                self._phase = Phase.READY
                self._append_event("info", "pressure in launch window")
            if self._phase == Phase.FLIGHT and self._sensors["fuel_level"] <= 0:
                self._phase = Phase.COAST
                self._append_event("info", "fuel exhausted, coasting")
            if (
                self._phase in (Phase.FLIGHT, Phase.COAST)
                and not self._apogee_reached
                and self._velocity <= 0
                and self._sensors["altitude"] > 20
            ):
                self._phase = Phase.APOGEE
                self._apogee_reached = True
                self._append_event("info", f"apogee {self._sensors['altitude']:.0f} m")
            if self._phase == Phase.DESCENT and self._sensors["altitude"] <= GROUND:
                self._phase = Phase.LANDED
                self._append_event("info", "landed")

    def _append_event(self, level: EventLevel, text: str) -> None:
        self._events.append((time.strftime("%H:%M:%S"), level, text))


def _check_intake(snapshot: MissionSnapshot, name: str, position: int) -> list[str]:
    if position >= OPEN_POSITION:
        return []
    sensors = snapshot["sensors"]
    if name == "fuel_intake" and sensors["oxidizer_level"] < TANK_FULL:
        raise Denied("oxidizer tank must be filled first")
    if snapshot["phase"] in LOADED_PHASES:
        raise Denied("intake valves stay closed after loading")
    if sensors[LEVELS[name]] >= TANK_FULL:
        raise Denied("tank already full")
    return []


def _check_main(snapshot: MissionSnapshot, _name: str, position: int) -> list[str]:
    if position >= OPEN_POSITION:
        return []
    sensors = snapshot["sensors"]
    servos = snapshot["servos"]
    pressure = sensors["oxidizer_pressure"]
    if snapshot["relays"]["igniter"]:
        raise Denied("igniter active, main valves locked")
    if sensors["fuel_level"] < TANK_FULL or sensors["oxidizer_level"] < TANK_FULL:
        raise Denied("tanks must be full before opening main valves")
    if any(servos[intake] < OPEN_POSITION for intake in INTAKES):
        raise Denied("close intake valves before ignition")
    if not PRESSURE_MIN <= pressure <= PRESSURE_MAX:
        raise Denied(f"pressure {pressure:.1f} bar outside ignition window {PRESSURE_MIN:.0f}-{PRESSURE_MAX:.0f}")
    if not THRUST_MIN <= pressure <= THRUST_MAX:
        return [f"pressure {pressure:.1f} bar, suboptimal thrust"]
    return []


def _check_igniter(snapshot: MissionSnapshot, on: bool) -> list[str]:
    if not on:
        return []
    servos = snapshot["servos"]
    if any(servos[intake] < OPEN_POSITION for intake in INTAKES):
        raise Denied("close intake valves before ignition")
    if any(servos[main] >= OPEN_POSITION for main in MAINS):
        raise Denied("open both main valves before igniter")
    pressure = snapshot["sensors"]["oxidizer_pressure"]
    if not PRESSURE_MIN <= pressure <= PRESSURE_MAX:
        raise Denied(f"pressure {pressure:.1f} bar outside ignition window {PRESSURE_MIN:.0f}-{PRESSURE_MAX:.0f}")
    return []


def _check_heater(snapshot: MissionSnapshot, on: bool) -> list[str]:
    if on and snapshot["phase"] in FLIGHT_PHASES:
        raise Denied("propulsion active, heater locked")
    return []


def _check_parachute(snapshot: MissionSnapshot, on: bool) -> list[str]:
    if not on:
        return []
    if snapshot["phase"] == Phase.FLIGHT and snapshot["sensors"]["fuel_level"] > 0:
        raise Denied("engine still burning")
    speed = abs(snapshot["velocity"])
    if speed > CHUTE_MAX_SPEED:
        raise Denied(f"speed {speed:.0f} m/s exceeds limit of {CHUTE_MAX_SPEED:.0f}")
    return []


SERVO_INTERLOCKS: dict[str, Callable[[MissionSnapshot, str, int], list[str]]] = {
    **dict.fromkeys(INTAKES, _check_intake),
    **dict.fromkeys(MAINS, _check_main),
}
RELAY_INTERLOCKS: dict[str, Callable[[MissionSnapshot, bool], list[str]]] = {
    "igniter": _check_igniter,
    "oxidizer_heater": _check_heater,
    "parachute": _check_parachute,
}


def check(snapshot: MissionSnapshot, kind: ActuatorKind, name: str, value: ActuatorValue) -> list[str]:
    try:
        if kind == "servo":
            return SERVO_INTERLOCKS[name](snapshot, name, int(value))
        return RELAY_INTERLOCKS[name](snapshot, bool(value))
    except KeyError as error:
        raise CommandError(f"unknown {kind}: {name}") from error
