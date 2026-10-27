"""Entry point for ``python -m agh_space_systems``.

Runs one of the packaged components: proxy, simulator or example.
"""

import sys

from agh_space_systems import example, proxy, simulator

COMPONENTS = {"proxy": proxy.main, "simulator": simulator.main, "example": example.main}


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] not in COMPONENTS:
        prog = "python -m agh_space_systems"
        sys.exit(f"usage: {prog} {{{','.join(COMPONENTS)}}} [component options]")
    COMPONENTS[args.pop(0)]()


if __name__ == "__main__":
    main()
