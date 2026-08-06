#!/usr/bin/env python3
"""Deterministically redirect native Xbox Cards Authentication to localhost.

The Xbox Cards module stores two 0x100-byte copies of its REST base URL in
each Authentication object.  ``Authentication`` formats ``%s/pow/auth`` from
the first copy.  The object exists only briefly, so polling for it loses a
race with the native request.

This reversible entry hook writes the local URL to both owned slots whenever
the real Authentication method runs, then resumes the displaced retail
instruction.  It also records the invocation count and object pointer.  It
does not submit Authentication, change a return value, publish an event, or
navigate the frontend.
"""

from __future__ import annotations

import argparse
import re

from fifa14_plain_send_hook import Xbdm, addi, addis, branch, insn, lwz, stw
import fifa14_pow_auth_trace as legacy_trace


MODULE_NAME = "powdllzf.xex.dll"
MODULE_BASE = 0x89700000

ROOT_GLOBAL = 0x897C3608
ROOT_VTABLE = 0x89708AE0
AUTH_OFFSET = 0x3A08
AUTH_VTABLE = 0x89707078
URL_OFFSET = 0x600
URL_SLOT_SIZE = 0x100
URL_SLOT_COUNT = 2
RETAIL_URL = b"http://pal.gt.easfc.ea.com:8094"

SITE = 0x897381E8
ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12

# Keep this hook separate from fifa14_pow_auth_trace's 0x897BF000 cave.  The
# apply path can safely retire that older passive entry hook before arming the
# deterministic redirect.  The credentials patch begins at 0x897BF200.
STUB = 0x897BF100
STUB_SIZE = 0xA0
JOURNAL = 0x897BF1A0
JOURNAL_SIZE = 0x20
URL_DATA = 0x897BF1C0
URL_DATA_SIZE = 0x20


def image(value: bytes) -> bytes:
    """Return the exact native 0x100-byte slot image (legacy/test helper)."""
    if not value or len(value) >= URL_SLOT_SIZE:
        raise ValueError("Cards REST base URL does not fit its native slot")
    return value + bytes(URL_SLOT_SIZE - len(value))


def url_data_image(value: bytes) -> bytes:
    """Return the bounded prefix copied by the entry hook."""
    if not value or len(value) >= URL_DATA_SIZE:
        raise ValueError("Cards REST base URL does not fit the hook data image")
    return value + bytes(URL_DATA_SIZE - len(value))


def pointer_load(register: int, address: int) -> list[int]:
    high = (address + 0x8000) >> 16
    low = address - (high << 16)
    return [addis(register, 0, high), addi(register, register, low)]


def build_stub(local_url: bytes) -> bytes:
    """Build an ABI-preserving logger and two-slot URL writer."""
    # Execute mflr r12 first: the next retail prologue instruction consumes
    # r12.  r10/r11 are volatile scratch registers and no argument is changed.
    words = [int.from_bytes(ORIGINAL, "big")]
    words.extend(pointer_load(11, JOURNAL))
    words.extend(
        (
            lwz(10, 11, 0),
            addi(10, 10, 1),
            stw(10, 11, 0),
            stw(3, 11, 4),
        )
    )
    words.extend(pointer_load(11, URL_DATA))
    for offset in range(0, len(url_data_image(local_url)), 4):
        words.extend(
            (
                lwz(10, 11, offset),
                stw(10, 3, URL_OFFSET + offset),
                stw(10, 3, URL_OFFSET + URL_SLOT_SIZE + offset),
            )
        )
    words.append(0x7C0004AC)  # sync before the retail method reads the slots
    tail = STUB + len(words) * 4
    words.append(branch(tail, SITE + 4, False))
    result = b"".join(insn(word) for word in words)
    if len(result) > STUB_SIZE:
        raise AssertionError("Cards auth endpoint hook exceeds its code cave")
    return result.ljust(STUB_SIZE, b"\0")


def site_patch() -> bytes:
    return insn(branch(SITE, STUB, False))


def verify_module(client: Xbdm) -> None:
    module = next(
        (
            line
            for line in client.multiline("modules")
            if re.search(r'name="powdllzf\.xex\.dll"', line, re.IGNORECASE)
        ),
        None,
    )
    if module is None or f"base=0x{MODULE_BASE:08x}" not in module.lower():
        raise RuntimeError(f"Unexpected or missing {MODULE_NAME}: {module}")


def live_auth(client: Xbdm) -> int | None:
    root = int.from_bytes(client.read(ROOT_GLOBAL, 4), "big")
    if not root:
        return None
    if int.from_bytes(client.read(root, 4), "big") != ROOT_VTABLE:
        raise RuntimeError(f"Unexpected CardsDLL root at 0x{root:08X}")
    auth = int.from_bytes(client.read(root + AUTH_OFFSET, 4), "big")
    if not auth:
        return None
    if int.from_bytes(client.read(auth, 4), "big") != AUTH_VTABLE:
        raise RuntimeError(f"Unexpected Cards Authentication object at 0x{auth:08X}")
    return auth


def slot_states(client: Xbdm, auth: int, local_url: bytes) -> list[str]:
    retail = image(RETAIL_URL)
    local = image(local_url)
    states: list[str] = []
    for index in range(URL_SLOT_COUNT):
        current = client.read(
            auth + URL_OFFSET + index * URL_SLOT_SIZE, URL_SLOT_SIZE
        )
        if current == retail:
            states.append("retail")
        elif current == local:
            states.append("local")
        else:
            states.append("unexpected")
    return states


def write_slots(client: Xbdm, auth: int, value: bytes) -> None:
    target = image(value)
    for index in range(URL_SLOT_COUNT):
        address = auth + URL_OFFSET + index * URL_SLOT_SIZE
        client.write(address, target)
        if client.read(address, URL_SLOT_SIZE) != target:
            raise RuntimeError(f"Cards REST URL verification failed at 0x{address:08X}")


def owned_hook_url(client: Xbdm) -> bytes | None:
    """Return the URL when the published hook is exactly one of our images."""
    if client.read(SITE, 4) != site_patch():
        return None
    raw = client.read(URL_DATA, URL_DATA_SIZE)
    value = raw.split(b"\0", 1)[0]
    try:
        value.decode("ascii")
    except UnicodeDecodeError:
        return None
    if not value.startswith(b"http://"):
        return None
    if client.read(STUB, STUB_SIZE) != build_stub(value):
        return None
    return value


def hook_state(client: Xbdm) -> tuple[str, bytes | None]:
    current = client.read(SITE, 4)
    if current == ORIGINAL:
        return "original", None
    if current == site_patch():
        value = owned_hook_url(client)
        return ("armed", value) if value is not None else ("unexpected", None)
    legacy_patch = insn(branch(SITE, legacy_trace.STUB, False))
    if current == legacy_patch:
        legacy_stub = legacy_trace.build_stub()
        if client.read(legacy_trace.STUB, len(legacy_stub)) == legacy_stub:
            return "legacy-trace", None
        return "unexpected", None
    return "unexpected", None


def describe(client: Xbdm, local_url: bytes) -> None:
    state, armed_url = hook_state(client)
    suffix = f" ({armed_url.decode('ascii')})" if armed_url else ""
    print(f"Cards auth endpoint hook = {state}{suffix}")
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    print(f"invocation_count         = {int.from_bytes(raw[0:4], 'big')}")
    print(f"auth_object              = 0x{int.from_bytes(raw[4:8], 'big'):08X}")
    auth = live_auth(client)
    if auth is None:
        print("live auth object          = absent")
    else:
        print(f"live auth object          = 0x{auth:08X}")
        print("REST URL slots            = " + ", ".join(slot_states(client, auth, local_url)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore", "read"))
    parser.add_argument("--local-ip", default="192.168.1.36")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()

    local_url = f"http://{args.local_ip}:{args.port}".encode("ascii")
    url_data_image(local_url)

    client = Xbdm(args.host)
    try:
        verify_module(client)
        state, armed_url = hook_state(client)
        if args.action in ("status", "read"):
            describe(client, local_url)
            return 0
        if state == "unexpected":
            raise RuntimeError("Refusing unexpected Cards Authentication entry hook")

        if args.action == "restore":
            if state == "armed":
                client.write(SITE, ORIGINAL)
                if client.read(SITE, 4) != ORIGINAL:
                    raise RuntimeError("Cards Authentication entry restore failed")
            auth = live_auth(client)
            if auth is not None:
                states = slot_states(client, auth, armed_url or local_url)
                if "unexpected" in states:
                    raise RuntimeError("Refusing unexpected live Cards REST URL slot")
                if "local" in states:
                    write_slots(client, auth, RETAIL_URL)
            print("Verified: retail Cards Authentication endpoint behavior restored.")
            return 0

        # Retire the older passive trace, or unpublish an existing instance
        # before changing its code/data image.  The branch is always installed
        # last, so the CPU never observes a partially written hook.
        if state in ("legacy-trace", "armed"):
            client.write(SITE, ORIGINAL)
            if client.read(SITE, 4) != ORIGINAL:
                raise RuntimeError("Could not unpublish the previous pow/auth hook")

        desired_stub = build_stub(local_url)
        existing_stub = client.read(STUB, STUB_SIZE)
        if existing_stub not in (bytes(STUB_SIZE), desired_stub):
            # A previously owned endpoint hook with another URL is safe to
            # replace only when it was recognized above.
            if state != "armed" or armed_url is None or existing_stub != build_stub(armed_url):
                raise RuntimeError("Cards auth endpoint hook cave is not free")

        client.write(JOURNAL, bytes(JOURNAL_SIZE))
        client.write(URL_DATA, url_data_image(local_url))
        client.write(STUB, desired_stub)
        if client.read(URL_DATA, URL_DATA_SIZE) != url_data_image(local_url):
            raise RuntimeError("Cards auth endpoint URL data verification failed")
        if client.read(STUB, STUB_SIZE) != desired_stub:
            raise RuntimeError("Cards auth endpoint stub verification failed")
        client.write(SITE, site_patch())
        if owned_hook_url(client) != local_url:
            raise RuntimeError("Cards auth endpoint hook verification failed")

        # If an object already exists, patch it too.  This closes the tiny gap
        # between publishing the hook and the next native method entry, while
        # still allowing apply before the object has been constructed.
        auth = live_auth(client)
        if auth is not None:
            states = slot_states(client, auth, local_url)
            if "unexpected" in states:
                raise RuntimeError("Refusing unexpected live Cards REST URL slot")
            if "retail" in states:
                write_slots(client, auth, local_url)

        print(
            "Verified: native Cards Authentication endpoint hook armed -> "
            f"{local_url.decode('ascii')}"
        )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
