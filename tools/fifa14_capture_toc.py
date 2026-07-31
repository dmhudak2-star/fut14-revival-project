#!/usr/bin/env python3
"""Capture FIFA 14's PPC TOC register when its thread trampoline runs."""

from __future__ import annotations

import argparse
import re
import select
import socket
import sys
import time


BREAKPOINT = 0x824BB760


class Connection:
    def __init__(self, host: str, timeout: float = 8):
        self.sock = socket.create_connection((host, 730), timeout=timeout)
        self.reader = self.sock.makefile("rb")
        if not self.line().startswith("201-"):
            raise RuntimeError("Unexpected XBDM banner")

    def line(self) -> str:
        raw = self.reader.readline()
        if not raw:
            raise EOFError("XBDM closed the connection")
        return raw.decode("ascii", "replace").rstrip("\r\n")

    def command(self, command: str, expected: int = 200) -> str:
        self.sock.sendall((command + "\r\n").encode("ascii"))
        while True:
            response = self.line()
            match = re.match(r"^(\d{3})[- ]", response)
            if match:
                if int(match.group(1)) != expected:
                    raise RuntimeError(f"{command}: {response}")
                return response

    def multiline(self, command: str) -> list[str]:
        self.sock.sendall((command + "\r\n").encode("ascii"))
        status = self.line()
        if not status.startswith("202-"):
            raise RuntimeError(f"{command}: {status}")
        result = []
        while True:
            line = self.line()
            if line == ".":
                return result
            result.append(line)

    def close(self) -> None:
        self.reader.close()
        self.sock.close()


def event_fields(line: str) -> dict[str, str]:
    return {
        key.lower(): value.strip('"')
        for key, value in re.findall(r'(\w+)=("[^"]*"|\S+)', line)
    }


def context(lines: list[str]) -> dict[str, str]:
    result = {}
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip().lower()] = value.strip()
    return result


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
            'debugger connect override name="FIFATOC" user="CodexMac"'
        )
        notify.command("notify reconnectport=1", expected=205)

        control = Connection(args.host)
        control.command(f"break addr=0x{BREAKPOINT:08X}")
        armed = True
        print(f"FIFA thread trampoline armed at 0x{BREAKPOINT:08X}.")
        print("Launch FIFA 14 now.", flush=True)

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            readable, _, _ = select.select([notify.sock], [], [], 1)
            if not readable:
                continue
            event = notify.line()
            values = event_fields(event)
            if (
                event.lower().startswith("break ")
                and int(values.get("addr", "0"), 0) == BREAKPOINT
            ):
                thread = int(values["thread"], 0)
                registers = context(
                    control.multiline(
                        f"getcontext thread=0x{thread:08X} control int"
                    )
                )
                print(f"Hit on thread 0x{thread:08X}")
                print(f"IAR={registers.get('iar', '?')}")
                print(f"LR={registers.get('lr', '?')}")
                print(f"GPR2={registers.get('gpr2', '?')}")
                return 0

        raise TimeoutError("No FIFA thread reached the trampoline")
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
