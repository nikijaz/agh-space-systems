from __future__ import annotations

import argparse
import os
import select
import sys
import threading
import time
from collections import deque
from collections.abc import Iterator, Sequence
from typing import Callable

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agh_space_systems.mission import (
    GROUND,
    OPEN_POSITION,
    PRESSURE_LIMIT,
    RELAYS,
    SENSOR_NAMES,
    SERVO_NAMES,
    SERVOS,
    TANK_FULL,
    THRUST_MAX,
    THRUST_MIN,
    ActuatorKind,
    ActuatorValue,
    CommandError,
    Denied,
    MissionSnapshot,
    MissionState,
    Phase,
    check,
)
from communication_library import ids
from communication_library.communication_manager import CommunicationManager
from communication_library.exceptions import CommunicationError, TransportTimeoutError, UnregisteredCallbackError
from communication_library.frame import Frame
from communication_library.tcp_transport import TcpSettings
from communication_library.transport import TransportType

HELP = """\
  status                  show current telemetry
  tank ox|fuel            fill a tank
  heat on|off             oxidizer heater
  servo <name> <0-100>    0 = open, 100 = closed
  relay <name> on|off
  launch                  open main valves and ignite
  chute                   deploy the parachute
  auto                    run the full mission
  abort                   stop the running sequence
  help / quit\
"""

PHASE_STYLE: dict[Phase, str] = {
    Phase.IDLE: "dim",
    Phase.FILLING_OX: "yellow",
    Phase.FILLING_FUEL: "yellow",
    Phase.HEATING: "yellow",
    Phase.READY: "bold green",
    Phase.IGNITION: "bold yellow",
    Phase.FLIGHT: "bold cyan",
    Phase.COAST: "cyan",
    Phase.APOGEE: "magenta",
    Phase.DESCENT: "cyan",
    Phase.LANDED: "bold green",
    Phase.FAILED: "bold red",
}
EVENT_STYLE = {"info": "white", "warn": "yellow", "error": "bold red"}
PendingKey = tuple[int, int, int]
CommandHandler = Callable[[list[str]], bool]


def servo_frame(name: str, position: int) -> Frame:
    return Frame(
        ids.BoardID.ROCKET,
        ids.PriorityID.HIGH,
        ids.ActionID.SERVICE,
        ids.BoardID.SOFTWARE,
        ids.DeviceID.SERVO,
        SERVOS[name],
        ids.DataTypeID.INT16,
        ids.OperationID.SERVO.value.POSITION,
        (position,),
    )


def relay_frame(name: str, on: bool) -> Frame:
    operation = ids.OperationID.RELAY.value.OPEN if on else ids.OperationID.RELAY.value.CLOSE
    return Frame(
        ids.BoardID.ROCKET,
        ids.PriorityID.HIGH,
        ids.ActionID.SERVICE,
        ids.BoardID.SOFTWARE,
        ids.DeviceID.RELAY,
        RELAYS[name],
        ids.DataTypeID.NO_DATA,
        operation,
        (),
    )


class Pending:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.status: int | None = None


class Aborted(Exception):
    pass


class Dashboard:
    def __init__(self, state: MissionState, console: Console) -> None:
        self.state = state
        self.console = console
        self.prompt = ""
        self._output_lock = threading.Lock()
        self._output: deque[tuple[str, str]] = deque(maxlen=100)

    def write(self, text: str, style: str = "white") -> None:
        with self._output_lock:
            self._output.append((text, style))

    def __rich__(self) -> Layout:
        snapshot = self.state.snapshot()
        sensors = snapshot["sensors"]

        header = Text()
        header.append("ROCKET MISSION CONTROL", style="bold")
        header.append(f"   {snapshot['phase'].value}", style=PHASE_STYLE[snapshot["phase"]])
        header.append("   link ", style="dim")
        link_style = "green" if snapshot["link_up"] else "bold red"
        header.append("up" if snapshot["link_up"] else "down", style=link_style)

        telemetry = Table.grid(padding=(0, 1))
        telemetry.add_column(style="dim", width=9)
        telemetry.add_column(justify="right", width=7)
        telemetry.add_column(style="dim")
        telemetry.add_row("fuel", f"{sensors['fuel_level']:.1f}", "%")
        telemetry.add_row("oxidizer", f"{sensors['oxidizer_level']:.1f}", "%")
        telemetry.add_row("pressure", f"{sensors['oxidizer_pressure']:.1f}", "bar")
        telemetry.add_row("altitude", f"{sensors['altitude']:.1f}", "m")
        telemetry.add_row("velocity", f"{snapshot['velocity']:+.1f}", "m/s")
        telemetry.add_row("angle", f"{sensors['angle']:.1f}", "deg")

        devices = Table.grid(padding=(0, 1))
        devices.add_column(style="dim", width=16)
        devices.add_column(width=6)
        for name, position in snapshot["servos"].items():
            is_open = position < OPEN_POSITION
            devices.add_row(name, Text("open" if is_open else "closed", style="green" if is_open else "dim"))
        for name, on in snapshot["relays"].items():
            devices.add_row(name, Text("on" if on else "off", style="green" if on else "dim"))

        events = Table.grid(expand=True)
        events.add_column(no_wrap=True)
        for timestamp, level, text in snapshot["events"]:
            events.add_row(Text(f"{timestamp}  {text}", style=EVENT_STYLE[level]))

        visible = max(1, self.console.height - 5)
        with self._output_lock:
            output = tuple(self._output)[-visible:]
        command_output = Table.grid(expand=True)
        command_output.add_column(no_wrap=True)
        for text, style in output:
            command_output.add_row(Text(text, style=style))

        prompt = Text("mcc> ", style="bold")
        prompt.append(self.prompt)
        prompt.append(" ", style="reverse")

        layout = Layout()
        layout.split_column(Layout(header, name="header", size=1), Layout(name="body", ratio=1))
        layout["body"].split_row(Layout(name="left", ratio=1), Layout(name="right", ratio=1))
        layout["left"].split_column(
            Layout(Panel(telemetry, title="telemetry", border_style="blue"), size=8),
            Layout(Panel(devices, title="devices", border_style="blue"), size=9),
            Layout(Panel(events, title="events", border_style="blue"), ratio=1),
        )
        layout["right"].split_column(
            Layout(Panel(command_output, title="commands and output", border_style="blue"), ratio=1),
            Layout(prompt, size=1),
        )
        return layout


class MissionController:
    def __init__(self, address: str, port: int) -> None:
        self.state = MissionState()
        self.manager = CommunicationManager()
        self.manager.change_transport_type(TransportType.TCP)
        self.manager.connect(TcpSettings(address, port))
        self.console = Console()
        self.dashboard = Dashboard(self.state, self.console)
        self.pending: dict[PendingKey, Pending] = {}
        self._pending_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._command_lock = threading.Lock()
        self._abort = threading.Event()
        self._stop = threading.Event()
        self._job: threading.Thread | None = None
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._frame_handlers = {
            (int(ids.ActionID.FEED), int(ids.DeviceID.SENSOR)): self._handle_sensor,
            (int(ids.ActionID.FEED), int(ids.DeviceID.SERVO)): self._handle_servo,
        }
        self._commands: dict[str, CommandHandler] = {
            "status": self._command_status,
            "tank": self._command_tank,
            "heat": self._command_heat,
            "servo": self._command_servo,
            "relay": self._command_relay,
            "launch": self._command_launch,
            "chute": self._command_chute,
            "auto": self._command_auto,
            "abort": self._command_abort,
            "help": self._command_help,
            "quit": self._command_quit,
            "exit": self._command_quit,
            "q": self._command_quit,
        }
        self._phase_transitions = {
            ("servo", "oxidizer_intake", True): Phase.FILLING_OX,
            ("servo", "fuel_intake", True): Phase.FILLING_FUEL,
            ("servo", "fuel_main", True): Phase.IGNITION,
            ("servo", "oxidizer_main", True): Phase.IGNITION,
            ("relay", "oxidizer_heater", True): Phase.HEATING,
            ("relay", "igniter", True): Phase.FLIGHT,
            ("relay", "parachute", True): Phase.DESCENT,
        }

    def start(self) -> None:
        self.state.log("info", "link established")
        self._reader.start()

    def close(self) -> None:
        self._stop.set()
        self._abort.set()
        try:
            self.manager.disconnect()
        except (CommunicationError, OSError):
            pass
        self._reader.join(timeout=1)

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self.manager.receive()
            except TransportTimeoutError:
                continue
            except UnregisteredCallbackError as error:
                frame = error.frame
            except (CommunicationError, OSError):
                self.state.set_link(False)
                if self.state.snapshot()["phase"] not in (Phase.LANDED, Phase.FAILED):
                    self.state.set_phase(Phase.FAILED)
                self.state.log("error", "link lost")
                break
            self._handle_frame(frame)

    def _handle_frame(self, frame: Frame) -> None:
        key = (int(frame.action), int(frame.device_type))
        handler = self._frame_handlers.get(key)
        if handler is not None:
            handler(frame)
            return
        if frame.action in (ids.ActionID.ACK, ids.ActionID.NACK):
            pending_key = (int(frame.device_type), frame.device_id, frame.operation)
            with self._pending_lock:
                pending = self.pending.pop(pending_key, None)
            if pending is not None:
                pending.status = frame.action
                pending.event.set()

    def _handle_sensor(self, frame: Frame) -> None:
        name = SENSOR_NAMES.get(frame.device_id)
        if name is None:
            return
        value = frame.data
        if isinstance(value, tuple):
            return
        self.state.update_sensor(name, float(value))
        self.state.evaluate()
        self._watchdog()

    def _handle_servo(self, frame: Frame) -> None:
        name = SERVO_NAMES.get(frame.device_id)
        if name is None:
            return
        value = frame.data
        if not isinstance(value, tuple):
            self.state.update_servo(name, int(value))

    def _watchdog(self) -> None:
        snapshot = self.state.snapshot()
        pressure = snapshot["sensors"]["oxidizer_pressure"]
        if snapshot["relays"]["oxidizer_heater"] and pressure > PRESSURE_LIMIT:
            self.state.set_relay("oxidizer_heater", False)
            self.state.log("error", "pressure limit reached, heater cut off")
            self._send(relay_frame("oxidizer_heater", False), wait=False)

    def _send(self, frame: Frame, wait: bool = True, timeout: float = 3.0) -> None:
        key = (int(frame.device_type), frame.device_id, frame.operation)
        pending = Pending() if wait else None
        if pending is not None:
            with self._pending_lock:
                self.pending[key] = pending
        with self._send_lock:
            self.manager.push(frame)
            self.manager.send()
        if pending is None:
            return
        if not pending.event.wait(timeout):
            with self._pending_lock:
                self.pending.pop(key, None)
            raise CommandError("no acknowledgement from rocket")
        if pending.status == ids.ActionID.NACK:
            raise CommandError("command rejected (NACK)")

    def execute(self, kind: ActuatorKind, name: str, value: ActuatorValue) -> None:
        with self._command_lock:
            warnings = check(self.state.snapshot(), kind, name, value)
            frame = servo_frame(name, int(value)) if kind == "servo" else relay_frame(name, bool(value))
            self._send(frame)
            if kind == "servo":
                self.state.update_servo(name, int(value))
            else:
                self.state.set_relay(name, bool(value))
            for warning in warnings:
                self.state.log("warn", warning)
        action = f"{value}" if kind == "servo" else ("on" if value else "off")
        self.dashboard.write(f"ACK  {kind} {name} {action}", "green")
        active = value < OPEN_POSITION if kind == "servo" else bool(value)
        phase = self._phase_transitions.get((kind, name, active))
        if phase is not None:
            self.state.set_phase(phase)

    def _wait(self, predicate: Callable[[MissionSnapshot], bool], timeout: float, label: str) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._abort.is_set():
                raise Aborted()
            if predicate(self.state.snapshot()):
                return
            time.sleep(0.1)
        raise CommandError(f"timeout waiting for {label}")

    def _start_job(self, target: Callable[[], None]) -> None:
        if self._job is not None and self._job.is_alive():
            self.state.log("warn", "a sequence is already running")
            self.dashboard.write("A sequence is already running", "yellow")
            return
        self._abort.clear()
        self.dashboard.write("Sequence started", "cyan")
        self._job = threading.Thread(target=self._run_job, args=(target,), daemon=True)
        self._job.start()

    def _run_job(self, target: Callable[[], None]) -> None:
        try:
            target()
        except Aborted:
            self.state.log("warn", "sequence aborted")
            self.dashboard.write("Sequence aborted", "yellow")
        except Denied as error:
            message = f"Denied: {error}"
            self.state.log("error", message)
            self.dashboard.write(message, "bold red")
        except CommandError as error:
            self.state.log("error", str(error))
            self.dashboard.write(str(error), "bold red")

    def _fill(self, propellant: str) -> None:
        intake, level = {
            "ox": ("oxidizer_intake", "oxidizer_level"),
            "fuel": ("fuel_intake", "fuel_level"),
        }[propellant]
        self.execute("servo", intake, 0)
        self.state.log("info", f"filling {propellant}")
        self._wait(lambda snapshot: snapshot["sensors"][level] >= TANK_FULL, 90, f"{propellant} fill")
        self.execute("servo", intake, 100)
        self.state.log("info", f"{propellant} full")

    def _launch(self) -> None:
        self.execute("servo", "fuel_main", 0)
        time.sleep(0.2)
        self.execute("servo", "oxidizer_main", 0)
        time.sleep(0.2)
        self.execute("relay", "igniter", True)
        self.state.log("info", "liftoff")

    def _auto(self) -> None:
        self._fill("ox")
        self._fill("fuel")
        self.execute("relay", "oxidizer_heater", True)
        self.state.log("info", "heating")
        self._wait(
            lambda snapshot: THRUST_MIN <= snapshot["sensors"]["oxidizer_pressure"] <= THRUST_MAX,
            120,
            "launch pressure",
        )
        self.execute("relay", "oxidizer_heater", False)
        self._launch()
        self._wait(lambda snapshot: snapshot["sensors"]["fuel_level"] <= 0, 120, "fuel burn")
        self._wait(lambda snapshot: snapshot["apogee_reached"], 120, "apogee")
        self.execute("relay", "parachute", True)
        self.state.log("info", "parachute deployed")
        self._wait(lambda snapshot: snapshot["sensors"]["altitude"] <= GROUND, 180, "landing")
        self.state.log("info", "mission complete")

    def _do_abort(self) -> None:
        self._abort.set()
        safe_commands: tuple[tuple[ActuatorKind, str, ActuatorValue], ...] = (
            ("relay", "oxidizer_heater", False),
            ("servo", "fuel_main", 100),
            ("servo", "oxidizer_main", 100),
        )
        for command in safe_commands:
            try:
                self.execute(*command)
            except (Denied, CommandError):
                continue
        self.state.log("warn", "abort: sequence stopped")

    @staticmethod
    def _expect_args(args: Sequence[str], count: int, usage: str) -> None:
        if len(args) != count:
            raise CommandError(f"usage: {usage}")

    def dispatch(self, line: str) -> bool:
        parts = line.split()
        if not parts:
            return True
        self.dashboard.write(f"mcc> {line}", "bold cyan")
        command = self._commands.get(parts[0].lower())
        if command is None:
            message = f"Unknown command: {parts[0]}"
            self.state.log("error", message)
            self.dashboard.write(message, "bold red")
            return True
        try:
            return command(parts[1:])
        except Denied as error:
            message = f"Denied: {error}"
            self.state.log("error", message)
            self.dashboard.write(message, "bold red")
        except (CommandError, IndexError, KeyError, ValueError) as error:
            message = str(error) or "Invalid arguments; use help"
            self.state.log("error", message)
            self.dashboard.write(message, "bold red")
        return True

    def _command_status(self, args: list[str]) -> bool:
        self._expect_args(args, 0, "status")
        snapshot = self.state.snapshot()
        sensors = snapshot["sensors"]
        self.dashboard.write(
            f"{snapshot['phase'].value}: fuel {sensors['fuel_level']:.1f}%, "
            f"oxidizer {sensors['oxidizer_level']:.1f}%, pressure {sensors['oxidizer_pressure']:.1f} bar",
            "cyan",
        )
        self.dashboard.write(
            f"altitude {sensors['altitude']:.1f} m, velocity {snapshot['velocity']:+.1f} m/s",
            "cyan",
        )
        return True

    def _command_tank(self, args: list[str]) -> bool:
        self._expect_args(args, 1, "tank ox|fuel")
        if args[0] not in ("ox", "fuel"):
            raise CommandError("usage: tank ox|fuel")
        self._start_job(lambda: self._fill(args[0]))
        return True

    def _command_heat(self, args: list[str]) -> bool:
        self._expect_args(args, 1, "heat on|off")
        self.execute("relay", "oxidizer_heater", self._parse_switch(args[0]))
        return True

    def _command_servo(self, args: list[str]) -> bool:
        self._expect_args(args, 2, "servo <name> <0-100>")
        position = int(args[1])
        if not 0 <= position <= 100:
            raise CommandError("servo position must be between 0 and 100")
        self.execute("servo", args[0], position)
        return True

    def _command_relay(self, args: list[str]) -> bool:
        self._expect_args(args, 2, "relay <name> on|off")
        self.execute("relay", args[0], self._parse_switch(args[1]))
        return True

    def _command_launch(self, args: list[str]) -> bool:
        self._expect_args(args, 0, "launch")
        self._start_job(self._launch)
        return True

    def _command_chute(self, args: list[str]) -> bool:
        self._expect_args(args, 0, "chute")
        self.execute("relay", "parachute", True)
        return True

    def _command_auto(self, args: list[str]) -> bool:
        self._expect_args(args, 0, "auto")
        self._start_job(self._auto)
        return True

    def _command_abort(self, args: list[str]) -> bool:
        self._expect_args(args, 0, "abort")
        self._do_abort()
        return True

    def _command_help(self, args: list[str]) -> bool:
        self._expect_args(args, 0, "help")
        for line in HELP.splitlines():
            self.dashboard.write(line)
        return True

    @staticmethod
    def _command_quit(args: list[str]) -> bool:
        MissionController._expect_args(args, 0, "quit")
        return False

    @staticmethod
    def _parse_switch(value: str) -> bool:
        switches = {"on": True, "off": False}
        try:
            return switches[value]
        except KeyError as error:
            raise CommandError("expected on or off") from error

    def _read_lines(self) -> Iterator[str]:
        if not sys.stdin.isatty():
            yield from sys.stdin
            return
        if os.name == "nt":
            yield from iter(input, "")
            return
        import termios
        import tty

        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        typed = ""
        escape = False
        try:
            while True:
                if not select.select([fd], [], [], 0.1)[0]:
                    continue
                data = os.read(fd, 64)
                if not data:
                    return
                for char in data.decode(errors="ignore"):
                    if escape:
                        escape = not (char.isalpha() or char == "~")
                        continue
                    if char == "\x1b":
                        escape = True
                    elif char in "\r\n":
                        line, typed = typed, ""
                        self.dashboard.prompt = ""
                        yield line
                    elif char in "\x7f\b":
                        typed = typed[:-1]
                    elif char in ("\x04", "\x03"):
                        return
                    elif char.isprintable():
                        typed += char
                    self.dashboard.prompt = typed
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    def repl(self) -> None:
        with Live(self.dashboard, console=self.console, refresh_per_second=10, screen=sys.stdin.isatty()):
            for line in self._read_lines():
                if not self.dispatch(line):
                    break


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default="127.0.0.1")
    parser.add_argument("--port", default=3000, type=int)
    args = parser.parse_args()
    try:
        controller = MissionController(args.address, args.port)
    except (CommunicationError, OSError) as error:
        print(f"connection failed: {error}")
        return
    controller.start()
    try:
        controller.repl()
    except KeyboardInterrupt:
        pass
    finally:
        controller.close()
