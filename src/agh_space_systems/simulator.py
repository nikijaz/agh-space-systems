import logging
import sys
import time
from argparse import ArgumentParser
from enum import Enum
from importlib import resources

import yaml

from communication_library.communication_manager import CommunicationManager, TransportType
from communication_library.exceptions import TransportTimeoutError, UnregisteredCallbackError
from communication_library.frame import Frame, ids
from communication_library.tcp_transport import TcpSettings


class SimulationState(Enum):
    IDLE = "IDLE"
    FILLING_OXIDIZER = "FILLING_OXIDIZER"
    OXIDIZER_FILLED = "OXIDIZER_FILLED"
    FILLING_FUEL = "FILLING_FUEL"
    FUEL_FILLED = "FUEL_FILLED"
    HEATING = "HEATING"
    READY_TO_LAUNCH = "READY_TO_LAUNCH"
    IGNITION_SEQUENCE = "IGNITION_SEQUENCE"
    FLIGHT = "FLIGHT"
    APOGEE = "APOGEE"
    PARACHUTE_DEPLOYED = "PARACHUTE_DEPLOYED"
    FREEFALL = "FREEFALL"
    EXPLOSION = "EXPLOSION"
    LANDED = "LANDED"


class StandaloneMock:
    def __init__(
        self,
        proxy_address: str,
        proxy_port: int,
        hardware_config: str,
        feed_send_interval: float,
        no_print: bool,
        verbose: bool,
        time_multiplier: float,
    ):

        with open(hardware_config, "r") as config_file:
            self.config = yaml.safe_load(config_file)

        self.manager = CommunicationManager()
        self.manager.change_transport_type(TransportType.TCP)
        self.manager.connect(TcpSettings(address=proxy_address, port=proxy_port))
        self.setup_loggers()
        self._logger = logging.getLogger("main")
        self.feed_send_delay = feed_send_interval
        self.no_print = no_print
        self.verbose = verbose
        self.time_multiplier = time_multiplier
        self.last_feed_update = time.perf_counter()
        self.last_physics_update = time.perf_counter()
        self.last_status_print = time.perf_counter()
        self.should_run = True

        self.state = SimulationState.IDLE

        self.servos = {}
        for servo_name, servo_config in self.config["devices"]["servo"].items():
            self.servos[servo_name] = servo_config["closed_pos"]

        self.relays = {}
        for relay_name in self.config["devices"]["relay"]:
            self.relays[relay_name] = 0

        self.sensors = {
            "fuel_level": 0.0,
            "oxidizer_level": 0.0,
            "altitude": 0.0,
            "oxidizer_pressure": 0.0,
            "angle": 2.0,
        }

        self.oxidizer_filled = False
        self.fuel_filled = False
        self.fuel_main_open_time = None
        self.oxidizer_main_open_time = None
        self.igniter_start_time = None
        self.apogee_reached_time = None
        self.max_altitude = 0.0
        self.velocity = 0.0
        self.thrust_multiplier = 1.0

        self._logger.info(f"Rocket simulator is running connected to {proxy_address}:{proxy_port}")
        self._logger.info(f"State: {self.state.value}")

    def setup_loggers(self):
        logger_main = logging.getLogger("main")
        logger_main.setLevel(logging.DEBUG)

        fmt = "[%(asctime)s] [%(levelname)s] %(message)s"
        log_formatter = logging.Formatter(fmt=fmt)

        console_handler = logging.StreamHandler(sys.stdout)

        console_handler.setFormatter(log_formatter)
        logger_main.addHandler(console_handler)

    def print_rocket_status(self):
        self._logger.info("=" * 60)
        self._logger.info("ROCKET STATUS:")
        self._logger.info(f"  State: {self.state.value}")
        self._logger.info("  Sensors:")
        self._logger.info(f"    - Fuel Level: {self.sensors['fuel_level']:.1f}%")
        self._logger.info(f"    - Oxidizer Level: {self.sensors['oxidizer_level']:.1f}%")
        self._logger.info(f"    - Oxidizer Pressure: {self.sensors['oxidizer_pressure']:.1f} bar")
        self._logger.info(f"    - Altitude: {self.sensors['altitude']:.1f} m")
        self._logger.info(f"    - Angle: {self.sensors['angle']:.1f}°")
        self._logger.info("  Servos:")
        for servo_name, position in self.servos.items():
            self._logger.info(f"    - {servo_name}: {position}")
        self._logger.info("  Relays:")
        for relay_name, state in self.relays.items():
            self._logger.info(f"    - {relay_name}: {'OPEN' if state else 'CLOSED'}")
        self._logger.info(f"  Velocity: {self.velocity:.2f} m/s")
        self._logger.info("=" * 60)

    def explode(self, reason: str):
        self.state = SimulationState.EXPLOSION
        self._logger.error(f"EXPLOSION: {reason}")
        self.print_rocket_status()
        self._logger.error("Simulation ended.")
        time.sleep(2)
        self.should_run = False

    def handle_frame(self, _frame) -> list[Frame]:
        output_frames = []
        handled = False

        if self.verbose:
            self._logger.info(f"Received frame: {_frame}")

        if _frame.device_type == ids.DeviceID.SERVO:
            servo_name = self.get_servo_name(_frame.device_id)
            if servo_name:
                if _frame.operation == ids.OperationID.SERVO.value.POSITION:
                    old_val = self.servos[servo_name]
                    new_position = int(_frame.data)
                    self.servos[servo_name] = new_position

                    servo_config = self.config["devices"]["servo"][servo_name]
                    open_pos = servo_config["open_pos"]
                    closed_pos = servo_config["closed_pos"]

                    self._logger.info(f"{servo_name} position set to {new_position} (was {old_val})")

                    if abs(new_position - open_pos) < abs(new_position - closed_pos):
                        if servo_name == "fuel_main":
                            self.fuel_main_open_time = time.perf_counter()
                        elif servo_name == "oxidizer_main":
                            self.oxidizer_main_open_time = time.perf_counter()
                    else:
                        if servo_name == "fuel_main":
                            self.fuel_main_open_time = None
                        elif servo_name == "oxidizer_main":
                            self.oxidizer_main_open_time = None

                    handled = True
                else:
                    self._logger.warning(f"Unknown servo operation {_frame.operation} for {servo_name}")
            else:
                self._logger.warning(f"Unknown servo device_id {_frame.device_id}")

            if handled:
                replacements = {"destination": _frame.source, "source": _frame.destination, "action": ids.ActionID.ACK}
                output_frames.append(Frame(**{**_frame.as_dict(), **replacements}))

        elif _frame.device_type == ids.DeviceID.RELAY:
            relay_name = self.get_relay_name(_frame.device_id)
            if relay_name:
                if _frame.operation == ids.OperationID.RELAY.value.OPEN:
                    old_val = self.relays[relay_name]
                    self.relays[relay_name] = 1
                    self._logger.info(f"{relay_name} relay opened (was {old_val}, now 1)")

                    if relay_name == "igniter":
                        self.igniter_start_time = time.perf_counter()

                    handled = True

                elif _frame.operation == ids.OperationID.RELAY.value.CLOSE:
                    old_val = self.relays[relay_name]
                    self.relays[relay_name] = 0
                    self._logger.info(f"{relay_name} relay closed (was {old_val}, now 0)")

                    if relay_name == "igniter":
                        self.igniter_start_time = None

                    handled = True
                else:
                    self._logger.warning(f"Unknown relay operation {_frame.operation} for {relay_name}")
            else:
                self._logger.warning(f"Unknown relay device_id {_frame.device_id}")

            if handled:
                replacements = {"destination": _frame.source, "source": _frame.destination, "action": ids.ActionID.ACK}
                output_frames.append(Frame(**{**_frame.as_dict(), **replacements}))

        else:
            self._logger.warning(f"Unknown device_type {_frame.device_type}")

        return output_frames

    def get_servo_name(self, device_id):
        for name, settings in self.config["devices"]["servo"].items():
            if settings["device_id"] == device_id:
                return name
        return None

    def get_relay_name(self, device_id):
        for name, settings in self.config["devices"]["relay"].items():
            if settings["device_id"] == device_id:
                return name
        return None

    def is_servo_open(self, servo_name: str) -> bool:
        servo_config = self.config["devices"]["servo"][servo_name]
        open_pos = servo_config["open_pos"]
        closed_pos = servo_config["closed_pos"]
        current_pos = self.servos[servo_name]

        threshold = abs(open_pos - closed_pos) * 0.3
        return abs(current_pos - open_pos) < threshold

    def update_physics(self, dt: float):
        if self.state == SimulationState.IDLE:
            if self.is_servo_open("fuel_intake"):
                self._logger.warning("PROPELLANT LOADING VIOLATION: Fuel intake opened before oxidizer is filled!")
                self._logger.warning("Correct procedure: Fill oxidizer tank first, then fuel tank.")
            elif self.is_servo_open("oxidizer_intake"):
                self.state = SimulationState.FILLING_OXIDIZER
                self._logger.info(f"State: {self.state.value}")
                self.print_rocket_status()

        elif self.state == SimulationState.FILLING_OXIDIZER:
            if self.is_servo_open("fuel_intake"):
                self._logger.warning(
                    "PROPELLANT LOADING VIOLATION: Fuel intake opened before oxidizer is fully filled!"
                )
                self._logger.warning("Correct procedure: Complete oxidizer filling first.")

            if self.is_servo_open("oxidizer_intake"):
                self.sensors["oxidizer_level"] = min(100.0, self.sensors["oxidizer_level"] + dt * 10.0)
                self.sensors["oxidizer_pressure"] = min(40.0, self.sensors["oxidizer_pressure"] + dt * 2.0)

                if self.sensors["oxidizer_level"] >= 100.0:
                    self.state = SimulationState.OXIDIZER_FILLED
                    self._logger.info(f"State: {self.state.value}")
                    self.print_rocket_status()
            else:
                if self.sensors["oxidizer_level"] >= 100.0:
                    self.state = SimulationState.OXIDIZER_FILLED
                    self._logger.info(f"State: {self.state.value}")
                    self.print_rocket_status()
                else:
                    self.sensors["oxidizer_pressure"] = max(0.0, self.sensors["oxidizer_pressure"] - dt * 1.0)

        elif self.state == SimulationState.OXIDIZER_FILLED:
            if self.relays["oxidizer_heater"] == 1:
                self.sensors["oxidizer_pressure"] = min(90.0, self.sensors["oxidizer_pressure"] + dt * 2.5)
                if self.sensors["oxidizer_pressure"] >= 90.0:
                    self.explode("Oxidizer pressure too high (90 bars) - tank explosion")
                    return
            else:
                self.sensors["oxidizer_pressure"] = max(30.0, self.sensors["oxidizer_pressure"] - dt * 1.0)

            if self.is_servo_open("fuel_intake"):
                self.state = SimulationState.FILLING_FUEL
                self._logger.info(f"State: {self.state.value}")
                self.print_rocket_status()

        elif self.state == SimulationState.FILLING_FUEL:
            if self.relays["oxidizer_heater"] == 1:
                self.sensors["oxidizer_pressure"] = min(90.0, self.sensors["oxidizer_pressure"] + dt * 2.5)
                if self.sensors["oxidizer_pressure"] >= 90.0:
                    self.explode("Oxidizer pressure too high (90 bars) - tank explosion")
                    return
            else:
                self.sensors["oxidizer_pressure"] = max(30.0, self.sensors["oxidizer_pressure"] - dt * 1.0)

            if self.is_servo_open("fuel_intake"):
                self.sensors["fuel_level"] = min(100.0, self.sensors["fuel_level"] + dt * 10.0)

                if self.sensors["fuel_level"] >= 100.0:
                    self.state = SimulationState.FUEL_FILLED
                    self._logger.info(f"State: {self.state.value}")
                    self.print_rocket_status()
            else:
                if self.sensors["fuel_level"] >= 100.0:
                    self.state = SimulationState.FUEL_FILLED
                    self._logger.info(f"State: {self.state.value}")
                    self.print_rocket_status()

        elif self.state == SimulationState.FUEL_FILLED:
            if self.relays["oxidizer_heater"] == 1:
                self.sensors["oxidizer_pressure"] = min(90.0, self.sensors["oxidizer_pressure"] + dt * 2.5)
                if self.sensors["oxidizer_pressure"] >= 90.0:
                    self.explode("Oxidizer pressure too high (90 bars) - tank explosion")
                    return
            else:
                self.sensors["oxidizer_pressure"] = max(30.0, self.sensors["oxidizer_pressure"] - dt * 1.0)

            if self.fuel_main_open_time and self.oxidizer_main_open_time:
                time_diff = abs(self.fuel_main_open_time - self.oxidizer_main_open_time)
                if time_diff > 1.0 and self.igniter_start_time:
                    self.explode("Main valves opened with >1s difference - propellant imbalance explosion")
                    return

                if self.igniter_start_time:
                    igniter_delay_fuel = abs(self.igniter_start_time - self.fuel_main_open_time)
                    igniter_delay_ox = abs(self.igniter_start_time - self.oxidizer_main_open_time)

                    if igniter_delay_fuel > 1.0 or igniter_delay_ox > 1.0:
                        self.explode("Igniter started >1s after main valves - engine flooded")
                        return

                    if self.igniter_start_time < min(self.fuel_main_open_time, self.oxidizer_main_open_time):
                        self.explode("Igniter started before main valves - single propellant combustion")
                        return

                    if self.is_servo_open("fuel_intake") or self.is_servo_open("oxidizer_intake"):
                        self.explode("Intake valves still open during ignition - catastrophic pressure loss")
                        return

                    pressure = self.sensors["oxidizer_pressure"]

                    if pressure < 40.0:
                        self._logger.error(
                            f"Ignition failed: Oxidizer pressure too low ({pressure:.1f} bars) - engine won't ignite"
                        )
                        self.igniter_start_time = None
                        return

                    if pressure > 65.0:
                        self.explode(f"Oxidizer pressure too high at ignition ({pressure:.1f} bars) - engine explosion")
                        return

                    if 55.0 <= pressure <= 65.0:
                        self.thrust_multiplier = 1.0
                        self._logger.info(f"Optimal pressure {pressure:.1f} bars - full thrust!")
                    else:
                        pressure_deviation = min(abs(pressure - 55.0), abs(pressure - 65.0))
                        self.thrust_multiplier = max(0.5, 1.0 - (pressure_deviation / 15.0) * 0.5)
                        self._logger.warning(
                            f"Suboptimal pressure {pressure:.1f} bars - thrust reduced to {self.thrust_multiplier * 100:.0f}%"
                        )

                    self.state = SimulationState.FLIGHT
                    self._logger.info(f"State: {self.state.value} - Engine ignited successfully!")
                    self.print_rocket_status()

        elif self.state == SimulationState.FLIGHT:
            if self.sensors["fuel_level"] > 0:
                if self.relays["parachute"] == 1:
                    self.explode("Parachute opened while engine is running - structural failure")
                    return

                burn_rate = dt * 8.0
                self.sensors["fuel_level"] = max(0.0, self.sensors["fuel_level"] - burn_rate)
                self.sensors["oxidizer_level"] = max(0.0, self.sensors["oxidizer_level"] - burn_rate)
                self.sensors["oxidizer_pressure"] = max(30.0, self.sensors["oxidizer_pressure"] - dt * 3.0)

                thrust = 15.0 * self.thrust_multiplier
                gravity = 9.81
                acceleration = thrust - gravity
                self.velocity += acceleration * dt
                self.sensors["altitude"] += self.velocity * dt

                self.sensors["angle"] = min(30.0, self.sensors["angle"] + dt * 2.0)
            else:
                if self.relays["parachute"] == 1:
                    if self.velocity > 30.0:
                        self._logger.error(
                            f"Parachute deployed at too high velocity ({self.velocity:.1f} m/s) during ascent - parachute ripped!"
                        )
                        self._logger.error("Continuing ballistic trajectory...")
                    else:
                        self.state = SimulationState.PARACHUTE_DEPLOYED
                        self._logger.info(f"State: {self.state.value} - Early parachute deployment")
                        self.print_rocket_status()
                        return

                self.velocity -= 9.81 * dt
                self.sensors["altitude"] += self.velocity * dt

                self.sensors["angle"] = min(90.0, self.sensors["angle"] + dt * 15.0)

                self.max_altitude = max(self.max_altitude, self.sensors["altitude"])

                if self.velocity <= 0 and self.apogee_reached_time is None:
                    self.apogee_reached_time = time.perf_counter()
                    self.state = SimulationState.APOGEE
                    self._logger.info(f"State: {self.state.value} - Maximum altitude: {self.sensors['altitude']:.2f}m")
                    self.print_rocket_status()

        elif self.state == SimulationState.APOGEE:
            time_since_apogee = time.perf_counter() - self.apogee_reached_time

            self.sensors["angle"] = min(180.0, self.sensors["angle"] + dt * 20.0)

            if self.relays["parachute"] == 1:
                self.state = SimulationState.PARACHUTE_DEPLOYED
                self._logger.info(f"State: {self.state.value}")
                self.print_rocket_status()
            elif time_since_apogee > 10.0:
                self.state = SimulationState.FREEFALL
                self._logger.info(f"State: {self.state.value} - Parachute not deployed in time!")
                self.print_rocket_status()
            else:
                self.velocity -= 9.81 * dt
                self.sensors["altitude"] += self.velocity * dt

        elif self.state == SimulationState.PARACHUTE_DEPLOYED:
            terminal_velocity = -5.0
            self.velocity = max(terminal_velocity, self.velocity - 9.81 * dt)
            self.sensors["altitude"] += self.velocity * dt

            if self.sensors["angle"] > 0:
                self.sensors["angle"] = max(0.0, self.sensors["angle"] - dt * 30.0)
            elif self.sensors["angle"] < 0:
                self.sensors["angle"] = min(0.0, self.sensors["angle"] + dt * 30.0)

            if self.sensors["altitude"] <= 0:
                self.sensors["altitude"] = 0.0
                self.velocity = 0.0
                self.state = SimulationState.LANDED
                self._logger.info(f"State: {self.state.value} - Successful landing!")
                self.print_rocket_status()
                time.sleep(2)
                self.should_run = False

        elif self.state == SimulationState.FREEFALL:
            self.velocity -= 9.81 * dt
            self.sensors["altitude"] += self.velocity * dt

            self.sensors["angle"] = min(180.0, self.sensors["angle"] + dt * 20.0)

            if self.relays["parachute"] == 1:
                if abs(self.velocity) > 30.0:
                    self._logger.error(
                        f"Parachute deployed at too high velocity ({abs(self.velocity):.1f} m/s) - parachute ripped!"
                    )
                    self._logger.error("Continuing freefall...")
                else:
                    self.state = SimulationState.PARACHUTE_DEPLOYED
                    self._logger.info(f"State: {self.state.value} - Late parachute deployment successful")
                    self.print_rocket_status()

            if self.sensors["altitude"] <= 0:
                self.sensors["altitude"] = 0.0
                self.velocity = 0.0
                self.state = SimulationState.LANDED
                self._logger.error(f"State: {self.state.value} - CRASH LANDING!")
                self.print_rocket_status()
                time.sleep(2)
                self.should_run = False

    def send_feed_frame(self):
        conf_dict = self.config
        sensors_config: dict = conf_dict["devices"]["sensor"]

        for sensor_name, sensor_settings in sensors_config.items():
            source = ids.BoardID[sensor_settings["board"].upper()]
            device_id = sensor_settings["device_id"]
            data_type = ids.DataTypeID[sensor_settings["data_type"].upper()]

            if sensor_name in self.sensors:
                value = self.sensors[sensor_name]
            else:
                value = 0.0

            frame = Frame(
                destination=ids.BoardID.SOFTWARE,
                priority=ids.PriorityID.LOW,
                action=ids.ActionID.FEED,
                source=source,
                device_type=ids.DeviceID.SENSOR,
                device_id=device_id,
                data_type=data_type,
                operation=ids.OperationID.SENSOR.value.READ,
                payload=(value,),
            )
            self.manager.push(frame)
            try:
                self.manager.send()
            except TransportTimeoutError:
                break

            if self.verbose:
                self._logger.info(f"sent feed frame: {frame}")

        servos_config: dict = conf_dict["devices"]["servo"]
        for servo_name, servo_settings in servos_config.items():
            source = ids.BoardID[servo_settings["board"].upper()]
            device_id = servo_settings["device_id"]
            data_type = ids.DataTypeID.INT16

            if servo_name in self.servos:
                value = int(self.servos[servo_name])
            else:
                value = 0

            frame = Frame(
                destination=ids.BoardID.SOFTWARE,
                priority=ids.PriorityID.LOW,
                action=ids.ActionID.FEED,
                source=source,
                device_type=ids.DeviceID.SERVO,
                device_id=device_id,
                data_type=data_type,
                operation=ids.OperationID.SERVO.value.POSITION,
                payload=(value,),
            )
            self.manager.push(frame)
            try:
                self.manager.send()
            except TransportTimeoutError:
                break

            if self.verbose:
                self._logger.info(f"sent feed frame: {frame}")

    def receive_send_loop(self):
        while self.should_run:
            current_time = time.perf_counter()

            if current_time > self.last_physics_update + 0.1:
                dt = (current_time - self.last_physics_update) * self.time_multiplier
                self.update_physics(dt)
                self.last_physics_update = current_time

            if not self.verbose and current_time > self.last_status_print + 1.0:
                self.print_rocket_status()
                self.last_status_print = current_time

            try:
                frame = self.manager.receive()
            except TransportTimeoutError:
                if current_time > self.last_feed_update + float(self.feed_send_delay):
                    self.send_feed_frame()
                    self.last_feed_update = current_time
                continue
            except UnregisteredCallbackError as e:
                frame = e.frame
            except KeyboardInterrupt:
                sys.exit()

            for response_frame in self.handle_frame(frame):
                self.manager.push(response_frame)
                if self.verbose:
                    self._logger.info(f"pushed frame: {response_frame}")
                try:
                    self.manager.send()
                except TransportTimeoutError:
                    continue

            if current_time > self.last_feed_update + float(self.feed_send_delay):
                self.send_feed_frame()
                self.last_feed_update = current_time


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--proxy-address", default="127.0.0.1")
    parser.add_argument("--proxy-port", default=3001)
    parser.add_argument("--feed-interval", default=1)
    parser.add_argument(
        "--hardware-config",
        default=None,
        help="Path to a hardware config YAML. Defaults to the bundled simulator_config.yaml.",
    )
    parser.add_argument("--no-print", default=False, action="store_true")
    parser.add_argument(
        "--verbose",
        default=False,
        action="store_true",
        help="Print all frames sent/received. If disabled, prints rocket status every second.",
    )
    parser.add_argument(
        "--time-multiplier",
        default=1.0,
        type=float,
        help="Simulation speed multiplier. 1.0 = real-time, 2.0 = 2x faster, 0.5 = 2x slower.",
    )
    cl_args = parser.parse_args()

    default_config = resources.files(__package__) / "data" / "simulator_config.yaml"
    standalone_mock = StandaloneMock(
        cl_args.proxy_address,
        int(cl_args.proxy_port),
        cl_args.hardware_config or str(default_config),
        cl_args.feed_interval,
        cl_args.no_print,
        cl_args.verbose,
        cl_args.time_multiplier,
    )
    standalone_mock.receive_send_loop()


if __name__ == "__main__":
    main()
