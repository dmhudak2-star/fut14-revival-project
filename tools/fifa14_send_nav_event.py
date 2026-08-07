#!/usr/bin/env python3
"""Send one validated ION navigation event through FIFA 14's native API."""

from __future__ import annotations

import argparse
import re
import socket

from fifa14_native_fut_auth_call import call, command
from fifa14_plain_send_hook import Xbdm, verify_module


NAV_INTERFACE_GLOBAL = 0x83DA4604
NAV_INTERFACE_VTABLE = 0x8206A64C
SEND_NAV_EVENT = 0x82805C10
FLOW_SERVICE_GLOBAL = 0x83D922B8
FLOW_VTABLE = 0x821B8A60
FLOW_DISPATCH = 0x8372BEF0

EVENTS = {
    "advance": 0x82077254,
    "advanceRequest": 0x8207FBB4,
    "createClub": 0x8212CE18,
    # This event exists in the active patched nav archive but not in static
    # default.xex rdata. JRPC2 supplies a temporary NUL-terminated byte array.
    "FUTStartUp": None,
    # futLogInFlow.nav gives futLogIn1 four transitions. "advance" leads to
    # futLogIn2, which is where the bootstrap stalls, and "createClub" leads to
    # the FutCreateClub screen whose loading popup never clears. "iceBreaker"
    # is the third, and it opens futIcebreakerFlow's futPackSelect -- the
    # captain chooser -- whose pack list and captain names this project's
    # server already serves. None of these strings are in static rdata.
    "iceBreaker": None,
    # futPackSelect leaves through GotoCreateClub, and createClub's own
    # "advance" reaches futLogIn2, whose advance exits the flow to futGamehub.
    "GotoCreateClub": None,
    "changeClubName": None,
}


def c_string(client: Xbdm, address: int, limit: int = 32) -> str:
    return client.read(address, limit).split(b"\0", 1)[0].decode("ascii")


def u32(client: Xbdm, address: int) -> int:
    return int.from_bytes(client.read(address, 4), "big")


def title_heap(pointer: int) -> bool:
    return 0xA0000000 <= pointer < 0xE0000000


def mixed_event_call(file, interface: int, event: str) -> str:
    payload = event.encode("ascii") + b"\0"
    request = (
        f"consolefeatures ver=2 type=1 as=0 "
        f'params="A\\{SEND_NAV_EVENT:X}\\A\\2\\'
        f"1\\{interface & 0xFFFFFFFF}\\"
        f"7/{len(payload)}\\{payload.hex().upper()}\\\""
    )
    response = command(file, request)
    for _ in range(16):
        match = re.search(r"buf_addr=(?:0x)?([0-9A-Fa-f]+)", response)
        if not match:
            return response
        response = command(
            file,
            "consolefeatures ver=2 "
            f"buf_addr=0x{int(match.group(1), 16):X}",
        )
    return "200- SendNavEvent dispatched; JRPC2 result remained pending"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("event", choices=tuple(EVENTS))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    event_pointer = EVENTS[args.event]
    memory = Xbdm(args.host)
    try:
        verify_module(memory)
        interface = u32(memory, NAV_INTERFACE_GLOBAL)
        if not title_heap(interface):
            raise RuntimeError(f"Invalid navigation interface: 0x{interface:08X}")
        vtable = u32(memory, interface)
        target = u32(memory, vtable + 0x14)
        service = u32(memory, FLOW_SERVICE_GLOBAL)
        ui = u32(memory, service + 0x78) if title_heap(service) else 0
        flow = u32(memory, ui + 0x18) if title_heap(ui) else 0
        flow_vtable = u32(memory, flow) if title_heap(flow) else 0
        flow_dispatch = u32(memory, flow_vtable + 0x0C) if flow_vtable else 0
        live_event = (
            c_string(memory, event_pointer)
            if event_pointer is not None
            else args.event
        )
    finally:
        memory.close()

    print(f"nav interface = 0x{interface:08X}")
    print(f"vtable        = 0x{vtable:08X}")
    print(f"vtable+0x14  = 0x{target:08X}")
    print(f"flow service  = 0x{service:08X}")
    print(f"flow UI       = 0x{ui:08X}")
    print(f"active flow   = 0x{flow:08X}")
    print(f"flow vtable   = 0x{flow_vtable:08X}")
    print(f"flow dispatch = 0x{flow_dispatch:08X}")
    location = (
        f"0x{event_pointer:08X}"
        if event_pointer is not None
        else "temporary JRPC2 byte array"
    )
    print(f"event         = {live_event!r} @ {location}")
    if vtable != NAV_INTERFACE_VTABLE or target != SEND_NAV_EVENT:
        raise RuntimeError("Unexpected live ION navigation binding")
    if not all(title_heap(pointer) for pointer in (service, ui, flow)):
        raise RuntimeError("ION flow service chain is not active")
    if flow_vtable != FLOW_VTABLE or flow_dispatch != FLOW_DISPATCH:
        raise RuntimeError("Unexpected active ION flow binding")
    if live_event != args.event:
        raise RuntimeError("Static navigation event string does not match")
    if args.dry_run:
        print("Verified: navigation binding and active flow are valid (dry run).")
        return 0

    with socket.create_connection((args.host, 730), timeout=8) as sock:
        sock.settimeout(20)
        file = sock.makefile("rwb", buffering=0)
        greeting = file.readline().decode("ascii", "replace").strip()
        if not greeting.startswith("201"):
            raise RuntimeError(f"Unexpected XBDM greeting: {greeting}")
        response = (
            call(file, SEND_NAV_EVENT, (interface, event_pointer))
            if event_pointer is not None
            else mixed_event_call(file, interface, args.event)
        )
    print(f"SendNavEvent  = {response}")
    print(f"Verified: native navigation event {args.event!r} dispatched once.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
