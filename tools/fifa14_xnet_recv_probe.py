#!/usr/bin/env python3
"""Capture FIFA 14 calling the correctly resolved XAM receive export."""

from __future__ import annotations

import argparse
import select
import sys
import time

from fifa14_dirtysock_recv_trace import Connection, context, fields, register


BREAKPOINT = 0x81741C78
TITLE_START = 0x82000000
TITLE_END = 0x83EC0000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    notify = Connection(args.host)
    control: Connection | None = None
    armed = False
    try:
        notify.command(
            'debugger connect override name="XNetRecvProbe" user="CodexMac"'
        )
        notify.command("notify reconnectport=1", expected=205)
        control = Connection(args.host)
        control.command(f"break addr=0x{BREAKPOINT:08X}")
        armed = True
        print(f"Correct XAM receive export armed at 0x{BREAKPOINT:08X}.")
        print("Open Ultimate Team now.", flush=True)

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            readable, _, _ = select.select([notify.sock], [], [], 1)
            if not readable:
                continue
            event = notify.line()
            values = fields(event)
            if (
                not event.lower().startswith("break ")
                or int(values.get("addr", "0"), 0) != BREAKPOINT
            ):
                continue

            thread = int(values["thread"], 0)
            registers = context(
                control.multiline(
                    f"getcontext thread=0x{thread:08X} control int"
                )
            )
            lr = register(registers["lr"])
            if not TITLE_START <= lr < TITLE_END:
                control.command("go")
                continue

            print(f"Hit on thread 0x{thread:08X}")
            for name in (
                "lr", "gpr3", "gpr4", "gpr5", "gpr6",
                "gpr7", "gpr8", "gpr9", "gpr10",
            ):
                print(f"{name.upper()}={registers.get(name, '?')}")
            return 0

        raise TimeoutError("FIFA did not call the corrected XAM receive export")
    finally:
        if control is not None:
            if armed:
                try:
                    control.command(f"break addr=0x{BREAKPOINT:08X} clear")
                    print("Breakpoint cleared.")
                except Exception as error:
                    print(f"Warning: cleanup failed: {error}", file=sys.stderr)
            try:
                control.command("go")
                print("Execution resumed.")
            except Exception:
                pass
            control.close()
        notify.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; cleanup attempted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"\nError: {error}", file=sys.stderr)
        raise SystemExit(1)
