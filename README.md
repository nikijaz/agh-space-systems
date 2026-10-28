# AGH Space Systems

Mission control CLI for the AGH Space Systems rocket simulator.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- Python 3.9 or newer (uv installs it if missing)

## Setup

```bash
uv sync
```

## Run

Start each component in its own terminal, in this order:

```bash
uv run ags-proxy
uv run ags-simulator --feed-interval 0.2
uv run ags-mcc
```

All components also run as `uv run python -m agh_space_systems proxy|simulator|example|mcc`, and each accepts `--help`.

## Mission control

At the `mcc>` prompt:

| Command | Action |
| --- | --- |
| `auto` | fly the whole mission: tanking, heating, ignition, parachute, landing |
| `tank ox\|fuel` | fill a tank |
| `heat on\|off` | oxidizer heater |
| `servo <name> <0-100>` | set a valve, 0 is open and 100 is closed |
| `relay <name> on\|off` | switch a relay |
| `launch` | open the main valves and ignite |
| `chute` | deploy the parachute |
| `abort` | stop the running sequence |
| `help`, `quit` | list commands, exit |

Unsafe commands are denied with a reason shown in the events panel.

## Notes

Keep the simulator at its default speed. `--time-multiplier` inflates the speed estimate used by the parachute interlock and makes `auto` fail at landing.
