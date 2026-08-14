#!/usr/bin/env python3
"""Locate a named CardsDLL binding in the mapped module, read-only.

The pack screen does not read a duplicate flag out of any server response. Two
strings in `.rdata` say why:

* `HAS_DUPLICATE` sits in a run of frontend property keys -- `CALLBACK`,
  `CONSUMABLE_TYPE`, `FIRST_WON`, `TOURNAMENT_ID`, `TIMES_WON`, `SUB_TYPE`,
  `IS_ACTIVE`, `HAS_DUPLICATE`;
* `GetCardDuplicate` sits in a run of native binding names --
  `GetPlayerCardInfo`, `GetSpecialCardInfo`, `GetCardDetails`,
  `GetCardCategory`, `GetUserCardInfo`, `GetCardDuplicate`.

So the screen asks CardsDLL and CardsDLL answers from its own state. This tool
finds where that answer is computed, which is the first thing needed to trace
it.

It reads the mapped module, finds the string, then finds the pointers to it.
A binding table entry is a name pointer next to a function pointer, so the
words around each hit are printed with the ones that look like code in this
module marked. Nothing is written and no hook is installed.

The same question is open for the captain selection: `fcc_login1` asks
`FUT_IcebreakerManager.SkipIceBreaker` before sending `createClub` instead of
`iceBreaker`, and that binding sits beside `HasUserDoneIB` and
`RetrieveUserActions`. `--icebreaker` points this at those three.

    python3 tools/fifa14_get_card_duplicate_locate.py 192.168.1.25
    python3 tools/fifa14_get_card_duplicate_locate.py 192.168.1.25 --icebreaker
    python3 tools/fifa14_get_card_duplicate_locate.py 192.168.1.25 --name BuildSquad

CardsDLL has to be mapped, which means being inside FUT: it unloads when the
session tears down.
"""

from __future__ import annotations

import argparse
import sys

from fifa14_plain_send_hook import Xbdm


MODULE = "CardsDLLzf.xex.dll"
DEFAULT_BASE = 0x89000000
DEFAULT_SIZE = 0x002B0000

# The duplicate flag is the default because it is what this was written for.
# The icebreaker names are the other open question: `SkipIceBreaker` is what
# fcc_login1 asks before sending `createClub` instead of `iceBreaker`, and
# `HasUserDoneIB` sits beside it in the same binding block.
DEFAULT_NAMES = ("GetCardDuplicate", "HAS_DUPLICATE")
ICEBREAKER_NAMES = ("SkipIceBreaker", "HasUserDoneIB", "RetrieveUserActions")


def module_extent(client: Xbdm) -> tuple[int, int]:
    """Where CardsDLL is mapped right now, or a clear failure."""
    for line in client.multiline("modules"):
        if MODULE.lower() not in line.lower():
            continue
        base = size = None
        for field in line.split():
            key, _, value = field.partition("=")
            if key == "base":
                base = int(value, 16)
            elif key == "size":
                size = int(value, 16)
        if base and size:
            return base, size
    raise SystemExit(
        f"{MODULE} is not mapped. It unloads when the FUT session tears down;\n"
        "enter FUT and try again -- its code is not in memory otherwise."
    )


def scan(client: Xbdm, base: int, size: int, chunk: int) -> bytes:
    """The module's bytes, or as many as it will give up."""
    parts: list[bytes] = []
    offset = 0
    while offset < size:
        length = min(chunk, size - offset)
        try:
            parts.append(client.read(base + offset, length))
        except Exception as error:  # noqa: BLE001 -- report and keep what we have
            print(f"read stopped at 0x{base + offset:08X}: {error}", file=sys.stderr)
            break
        offset += length
    return b"".join(parts)


def occurrences(image: bytes, base: int, needle: bytes) -> list[int]:
    found, start = [], 0
    while True:
        index = image.find(needle, start)
        if index < 0:
            return found
        found.append(base + index)
        start = index + 1


def pointers_to(image: bytes, base: int, target: int) -> list[int]:
    needle = target.to_bytes(4, "big")
    return [
        base + index
        for index in occurrences(image, 0, needle)
        if (base + index) % 4 == 0
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--chunk-size", type=lambda v: int(v, 0), default=0x8000)
    parser.add_argument("--window", type=int, default=4, help="words shown each side")
    parser.add_argument(
        "--name",
        action="append",
        dest="names",
        help="binding or property to locate; repeatable",
    )
    parser.add_argument(
        "--icebreaker",
        action="store_true",
        help=f"shorthand for {' '.join(ICEBREAKER_NAMES)}",
    )
    args = parser.parse_args()

    chosen = list(args.names or [])
    if args.icebreaker:
        chosen += [name for name in ICEBREAKER_NAMES if name not in chosen]
    names = tuple((name + "\0").encode("ascii") for name in (chosen or DEFAULT_NAMES))

    client = Xbdm(args.host)
    try:
        base, size = module_extent(client)
        print(f"{MODULE} mapped at 0x{base:08X}, 0x{size:X} bytes")
        image = scan(client, base, size, args.chunk_size)
        print(f"read 0x{len(image):X} bytes")
    finally:
        client.close()

    limit = base + len(image)
    for needle in names:
        label = needle.rstrip(b"\x00").decode("ascii")
        for address in occurrences(image, base, needle):
            print(f'\n"{label}" at 0x{address:08X}')
            refs = pointers_to(image, base, address)
            if not refs:
                print("  no aligned pointer to it inside the module")
                continue
            for ref in refs:
                print(f"  referenced from 0x{ref:08X}")
                start = ref - args.window * 4
                for offset in range(0, args.window * 8 + 4, 4):
                    word_at = start + offset
                    if not base <= word_at < limit - 4:
                        continue
                    word = int.from_bytes(
                        image[word_at - base : word_at - base + 4], "big"
                    )
                    marks = []
                    if word_at == ref:
                        marks.append("<- the name")
                    if base <= word < limit:
                        marks.append("in-module pointer")
                    print(f"    0x{word_at:08X}: 0x{word:08X} {' '.join(marks)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
