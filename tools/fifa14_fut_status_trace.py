#!/usr/bin/env python3
"""Capture the FIFA 14 FUT status value at a temporary XBDM breakpoint."""

from __future__ import annotations

import argparse
import re
import select
import socket
import sys
import time


BREAKPOINT = 0x828351A4  # instruction after bl 0x82782028; r3 holds FUT status


class Connection:
    def __init__(self, host: str, timeout: float):
        self.sock = socket.create_connection((host, 730), timeout=timeout)
        self.sock.settimeout(timeout)
        self.reader = self.sock.makefile("rb")
        banner = self.line()
        if not banner.startswith("201-"):
            raise RuntimeError(f"Unexpected XBDM banner: {banner}")

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
                status = int(match.group(1))
                if status != expected:
                    raise RuntimeError(f"{command}: {response}")
                return response

    def multiline(self, command: str) -> list[str]:
        self.sock.sendall((command + "\r\n").encode("ascii"))
        response = self.line()
        if not response.startswith("202-"):
            raise RuntimeError(f"{command}: {response}")
        lines: list[str] = []
        while True:
            line = self.line()
            if line == ".":
                return lines
            lines.append(line)

    def close(self) -> None:
        self.reader.close()
        self.sock.close()


def parse_fields(line: str) -> dict[str, str]:
    return {
        key.lower(): value.strip('"')
        for key, value in re.findall(r'(\w+)=("[^"]*"|\S+)', line)
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", help="Xbox 360 IP address")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    notify = Connection(args.host, 5)
    control: Connection | None = None
    breakpoint_set = False
    try:
        notify.command(
            'debugger connect override name="FUTTrace" user="CodexMac"',
            expected=200,
        )
        notify.command("notify reconnectport=1", expected=205)

        control = Connection(args.host, 5)
        control.command(f"break addr=0x{BREAKPOINT:08X}")
        breakpoint_set = True
        print(f"Breakpoint armed at 0x{BREAKPOINT:08X}.")
        print("Open Ultimate Team on the Xbox now.", flush=True)

        notify.sock.settimeout(None)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            readable, _, _ = select.select([notify.sock], [], [], min(1.0, remaining))
            if not readable:
                continue
            event = notify.line()
            print(f"XBDM: {event}", flush=True)
            if not event.lower().startswith("break "):
                continue
            fields = parse_fields(event)
            address = int(fields.get("addr", "0"), 0)
            if address != BREAKPOINT:
                continue
            thread = int(fields["thread"], 0)
            context_lines = control.multiline(
                f"getcontext thread=0x{thread:08X} control int fp"
            )
            context = {}
            for line in context_lines:
                if "=" in line:
                    key, value = line.split("=", 1)
                    context[key.strip().lower()] = value.strip()

            print("\nFUT status breakpoint captured:")
            for name in ("thread", "cia", "lr", "ctr", "r3", "r23", "r31"):
                if name == "thread":
                    print(f"  thread = 0x{thread:08X}")
                elif name in context:
                    print(f"  {name} = {context[name]}")
            if "r3" in context:
                status = int(context["r3"], 0)
                print(f"  decoded status = 0x{status:X}")
                print(
                    "  flags: "
                    + ", ".join(
                        f"bit{bit}={(status >> bit) & 1}" for bit in range(6)
                    )
                )
            return 0

        raise TimeoutError("Breakpoint was not hit within the capture window")
    finally:
        if control is not None:
            if breakpoint_set:
                try:
                    control.command(f"break addr=0x{BREAKPOINT:08X} clear")
                    print("Breakpoint cleared.")
                except Exception as error:
                    print(f"Warning: breakpoint cleanup failed: {error}", file=sys.stderr)
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
