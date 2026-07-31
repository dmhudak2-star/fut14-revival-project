#!/usr/bin/env python3
"""Identify FIFA 14's caller of XAM NetDll_WSARecvFrom through XBDM."""

from __future__ import annotations

import argparse
import re
import select
import socket
import sys
import time


BREAKPOINT = 0x81741D58
TITLE_START = 0x82000000
TITLE_END = 0x83EC0000


class Connection:
    def __init__(self, host: str, timeout: float = 5.0):
        self.sock = socket.create_connection((host, 730), timeout=timeout)
        self.sock.settimeout(timeout)
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
        response = self.line()
        if not response.startswith("202-"):
            raise RuntimeError(f"{command}: {response}")
        lines = []
        while True:
            line = self.line()
            if line == ".":
                return lines
            lines.append(line)

    def close(self) -> None:
        self.reader.close()
        self.sock.close()


def fields(line: str) -> dict[str, str]:
    return {
        key.lower(): value.strip('"')
        for key, value in re.findall(r'(\w+)=("[^"]*"|\S+)', line)
    }


def context_map(lines: list[str]) -> dict[str, str]:
    result = {}
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip().lower()] = value.strip()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    notify = Connection(args.host)
    control: Connection | None = None
    armed = False
    try:
        notify.command(
            'debugger connect override name="FUTWSAProbe" user="CodexMac"'
        )
        notify.command("notify reconnectport=1", expected=205)
        notify.sock.settimeout(None)

        control = Connection(args.host)
        control.command(f"break addr=0x{BREAKPOINT:08X}")
        armed = True
        print(f"NetDll_WSARecvFrom armed at 0x{BREAKPOINT:08X}.")
        print("Open Ultimate Team now.", flush=True)

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            readable, _, _ = select.select([notify.sock], [], [], min(1.0, remaining))
            if not readable:
                continue
            event = notify.line()
            if not event.lower().startswith("break "):
                continue
            event_fields = fields(event)
            if int(event_fields.get("addr", "0"), 0) != BREAKPOINT:
                continue

            thread = int(event_fields["thread"], 0)
            ctx = context_map(
                control.multiline(
                    f"getcontext thread=0x{thread:08X} control int"
                )
            )
            caller = int(ctx.get("lr", "0"), 0)
            if not TITLE_START <= caller < TITLE_END:
                control.command("go")
                continue
            print(f"Hit on thread 0x{thread:08X}")
            for register in ("lr", "r1", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10"):
                print(f"{register.upper()}={ctx.get(register, 'unknown')}")
            return 0

        raise TimeoutError("NetDll_WSARecvFrom was not called within the window")
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
