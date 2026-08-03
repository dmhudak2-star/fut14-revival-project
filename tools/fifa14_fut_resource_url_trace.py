#!/usr/bin/env python3
"""Capture or narrowly redirect the native ContentManager resource ``fut``.

The supported retail build routes the logical resource name ``fut`` to
``futBoot.xml`` and eventually calls 0x82985948 with raw C-string pointers:

    r4 = logical resource name
    r5 = fully composed URL/path
    r6 = asynchronous completion callback

This breakpoint-free entry trampoline records only calls whose resource name
is exactly ``fut``.  It copies the temporary URL before the native downloader
can release it.  In redirect mode it replaces only r5 with a local HTTP URL,
then replays the displaced ``mflr r12`` and leaves the native resource service,
operation dispatcher, downloader and asynchronous callback paths untouched.
"""

from __future__ import annotations

import argparse

from fifa14_plain_recv_hook import conditional_branch
from fifa14_plain_send_hook import (
    Xbdm,
    addi,
    addis,
    branch,
    cmpw,
    cmpwi,
    insn,
    lbz,
    lwz,
    or_register,
    stb,
    stw,
    verify_module,
    write_chunks,
)


SITE = 0x82985948
ORIGINAL = bytes.fromhex("7D8802A6")  # mflr r12

# Verified zero/mapped title padding below the older 0x83C88000+ diagnostics.
STUB = 0x83C86000
REDIRECT_URL = STUB + 0x180
STUB_END = 0x83C86200
JOURNAL = STUB_END
URL_BUFFER = JOURNAL + 0x40
URL_CAPACITY = 0x700
CAVE_END = URL_BUFFER + URL_CAPACITY + 1
JOURNAL_SIZE = CAVE_END - JOURNAL

FUT_WORD = 0x66757400  # b"fut\0"


def ori(ra: int, rs: int, immediate: int) -> int:
    return 0x60000000 | (rs << 21) | (ra << 16) | (immediate & 0xFFFF)


def build_stub(redirect_url: str | None = None) -> bytes:
    redirect_bytes = b""
    if redirect_url is not None:
        redirect_bytes = redirect_url.encode("ascii") + b"\0"
        if len(redirect_bytes) > STUB_END - REDIRECT_URL:
            raise ValueError("redirect URL exceeds its reserved cave slot")

    words = [
        int.from_bytes(ORIGINAL, "big"),       # preserve caller LR in r12
        cmpwi(4, 0),
        0,                                      # beq fallback
        lwz(10, 4, 0),
        addis(9, 0, FUT_WORD >> 16),
        ori(9, 9, FUT_WORD & 0xFFFF),
        cmpw(10, 9),
        0,                                      # bne fallback
        addis(11, 0, (JOURNAL + 0x8000) >> 16),
        addi(11, 11, JOURNAL & 0xFFFF),
        lwz(10, 11, 0x04),                     # reserved sequence
        addi(10, 10, 1),
        stw(10, 11, 0x04),
        addi(9, 0, 0),
        stw(9, 11, 0x00),                      # invalidate old capture
        stw(3, 11, 0x08),
        stw(4, 11, 0x0C),
        stw(5, 11, 0x10),
        stw(6, 11, 0x14),
        stw(12, 11, 0x18),                     # native caller LR
        cmpwi(5, 0),
        0,                                      # beq publish
        addi(9, 11, URL_BUFFER - JOURNAL),
        or_register(10, 5, 5),                  # preserve native r5
        # 0x700 fits in the signed immediate accepted by li/addi.
        addi(8, 0, URL_CAPACITY),
        0x7D0903A6,                             # mtctr r8
    ]

    copy_loop = len(words)
    words.extend(
        (
            lbz(8, 10, 0),
            stb(8, 9, 0),
            cmpwi(8, 0),
            0,                                  # beq publish
            addi(10, 10, 1),
            addi(9, 9, 1),
            0,                                  # bdnz copy_loop
            addi(8, 0, 0),
            stb(8, 9, 0),                       # terminate capped URL
        )
    )
    publish = len(words)
    words.extend(
        (
            lwz(10, 11, 0x04),
            0x7C0004AC,                         # sync
            stw(10, 11, 0x00),                  # commit complete capture
        )
    )
    if redirect_url is not None:
        words.extend(
            (
                addis(5, 0, (REDIRECT_URL + 0x8000) >> 16),
                addi(5, 5, REDIRECT_URL & 0xFFFF),
            )
        )
    fallback = len(words)
    words.extend(
        (
            int.from_bytes(ORIGINAL, "big"),   # replay displaced mflr r12
            0,                                  # b SITE + 4
        )
    )

    def address(index: int) -> int:
        return STUB + index * 4

    words[2] = conditional_branch(address(2), address(fallback), 12, 2)
    words[7] = conditional_branch(address(7), address(fallback), 4, 2)
    words[21] = conditional_branch(address(21), address(publish), 12, 2)
    words[copy_loop + 3] = conditional_branch(
        address(copy_loop + 3), address(publish), 12, 2
    )
    words[copy_loop + 6] = conditional_branch(
        address(copy_loop + 6), address(copy_loop), 16, 0
    )
    words[-1] = branch(address(len(words) - 1), SITE + 4, False)

    raw = b"".join(insn(word) for word in words)
    if len(raw) > REDIRECT_URL - STUB:
        raise AssertionError("FUT resource URL trace exceeds its stub slot")
    image = bytearray(raw.ljust(STUB_END - STUB, b"\0"))
    if redirect_bytes:
        start = REDIRECT_URL - STUB
        image[start : start + len(redirect_bytes)] = redirect_bytes
    return bytes(image)


STUB_BYTES = build_stub()
PATCH = insn(branch(SITE, STUB, False))


def hook_state(client: Xbdm, expected_stub: bytes = STUB_BYTES) -> str:
    current = client.read(SITE, 4)
    cave = client.read(STUB, len(STUB_BYTES))
    if current == ORIGINAL:
        if cave in (bytes(len(expected_stub)), expected_stub):
            return "original"
        return "original-with-foreign-cave"
    if current == PATCH and cave == expected_stub:
        return "armed"
    if current == PATCH:
        return "armed-with-different-stub"
    return f"unexpected:{current.hex().upper()}"


def arm(client: Xbdm, redirect_url: str | None = None) -> None:
    stub_bytes = build_stub(redirect_url)
    state = hook_state(client, stub_bytes)
    if state == "armed":
        print("FUT resource URL trace already armed.")
        return
    if state != "original":
        raise RuntimeError(f"FUT resource URL trace cannot arm from {state}")

    write_chunks(client, JOURNAL, bytes(JOURNAL_SIZE))
    write_chunks(client, STUB, stub_bytes)
    if client.read(STUB, len(stub_bytes)) != stub_bytes:
        raise RuntimeError("FUT resource URL trace stub verification failed")
    if client.read(SITE, 4) != ORIGINAL:
        raise RuntimeError("FUT downloader entry changed before publication")
    client.write(SITE, PATCH)
    if hook_state(client, stub_bytes) != "armed":
        try:
            client.write(SITE, ORIGINAL)
        except Exception:
            pass
        raise RuntimeError("FUT resource URL trace publication failed")
    if redirect_url is None:
        print(f"Verified: native FUT resource URL trace armed at 0x{SITE:08X}.")
    else:
        print(
            "Verified: native FUT resource redirect armed at "
            f"0x{SITE:08X} -> {redirect_url}"
        )


def read_capture(client: Xbdm) -> bool:
    raw = client.read(JOURNAL, JOURNAL_SIZE)
    committed = int.from_bytes(raw[0x00:0x04], "big")
    reserved = int.from_bytes(raw[0x04:0x08], "big")
    print(f"committed/reserved = {committed}/{reserved}")
    if not committed:
        print("No native 'fut' resource request captured.")
        return False

    manager = int.from_bytes(raw[0x08:0x0C], "big")
    name_pointer = int.from_bytes(raw[0x0C:0x10], "big")
    url_pointer = int.from_bytes(raw[0x10:0x14], "big")
    callback = int.from_bytes(raw[0x14:0x18], "big")
    caller_lr = int.from_bytes(raw[0x18:0x1C], "big")
    copied = raw[URL_BUFFER - JOURNAL :]
    url = copied.split(b"\0", 1)[0].decode("utf-8", errors="replace")
    print(f"manager       = 0x{manager:08X}")
    print(f"name pointer  = 0x{name_pointer:08X} ('fut')")
    print(f"URL pointer   = 0x{url_pointer:08X}")
    print(f"callback      = 0x{callback:08X}")
    print(f"caller LR     = 0x{caller_lr:08X}")
    print(f"captured URL  = {url!r}")
    return bool(url)


def restore(client: Xbdm) -> None:
    state = hook_state(client)
    if state == "original":
        print("FUT resource URL trace already restored.")
        return
    if state != "armed":
        raise RuntimeError(f"Refusing to restore trace from {state}")
    client.write(SITE, ORIGINAL)
    if client.read(SITE, 4) != ORIGINAL:
        raise RuntimeError("FUT resource URL trace restore failed")
    print("Verified: native FUT resource URL trace restored.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "read", "restore"))
    parser.add_argument(
        "--redirect-url",
        help="replace only the matching native fut resource path with this URL",
    )
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        expected_stub = build_stub(args.redirect_url)
        state = hook_state(client, expected_stub)
        print(f"FUT resource URL trace: {state}")
        if args.action == "apply":
            arm(client, args.redirect_url)
        elif args.action == "read":
            if state != "armed":
                raise RuntimeError("FUT resource URL trace is not armed")
            read_capture(client)
        elif args.action == "restore":
            restore(client)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
