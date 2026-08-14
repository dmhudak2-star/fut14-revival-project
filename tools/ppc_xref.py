#!/usr/bin/env python3
"""Cross-reference a PowerPC image: who calls what, and who names what.

Written to answer one question without touching the console again -- which of
the nine callers of CardsDLL's zeroing allocator is on the path that resumes a
cup -- and kept because every question of that shape needs the same two
indexes.

`work/cardsdll-text.bin` is a flat dump of CardsDLLzf at its load address, code
and strings together, so both indexes come out of the same bytes:

  **calls**      every `bl`, resolved to its target. A function's entry is the
                 address something branches *to* with the link bit set.
  **references** every `lis`/`addi` pair that materialises a constant. That is
                 how a PowerPC function names a string, and it is what turns
                 "where is `tournamentData` used" into an address.

Neither index needs symbols, relocations or a section table.

    tools/ppc_xref.py work/cardsdll-text.bin --base 0x89000000 --refs-to 0x8902b9ec
    tools/ppc_xref.py work/cardsdll-text.bin --base 0x89000000 --callers 0x8912dac8
    tools/ppc_xref.py work/cardsdll-text.bin --base 0x89000000 \
        --reaches 0x8912dac8 --from 0x89123456
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path


def words(data: bytes, base: int):
    for offset in range(0, len(data) - 3, 4):
        yield base + offset, int.from_bytes(data[offset:offset + 4], "big")


def branch_target(address: int, word: int) -> tuple[int, bool] | None:
    """Resolve an I-form branch. Returns (target, is_call) or None."""
    if (word >> 26) != 18:
        return None
    displacement = word & 0x03FFFFFC
    if displacement & 0x02000000:
        displacement -= 0x04000000
    absolute = bool(word & 2)
    target = displacement if absolute else address + displacement
    return target & 0xFFFFFFFF, bool(word & 1)


def build_calls(data: bytes, base: int) -> dict[int, list[int]]:
    """target -> the addresses that `bl` to it."""
    calls: dict[int, list[int]] = defaultdict(list)
    for address, word in words(data, base):
        resolved = branch_target(address, word)
        if resolved and resolved[1]:
            calls[resolved[0]].append(address)
    return calls


def build_refs(data: bytes, base: int, window: int = 8) -> dict[int, list[int]]:
    """constant -> the addresses that materialise it.

    `lis rX, hi` is `addis rX, r0, hi`, and the low half follows in `addi`,
    `ori` or the displacement of a load. Only the first two are indexed here:
    a load's displacement names a field of a structure far more often than it
    names a string, and the noise would swamp the answer.

    The low half of `addi` is signed, which is why the high half is often one
    greater than the constant's top sixteen bits.
    """
    refs: dict[int, list[int]] = defaultdict(list)
    pending: dict[int, tuple[int, int]] = {}
    for address, word in words(data, base):
        opcode = word >> 26
        rt = (word >> 21) & 0x1F
        ra = (word >> 16) & 0x1F
        immediate = word & 0xFFFF
        if opcode == 15 and ra == 0:            # lis rt, immediate
            pending[rt] = (address, immediate << 16)
            continue
        if opcode in {14, 24} and ra in pending:
            # `addi rt, ra, lo` far more often than `addi rt, rt, lo`: the high
            # half is loaded once into a scratch register and several low
            # halves are added off it. Requiring rt == ra found nothing at all.
            origin, high = pending[ra]
            if opcode == 14:
                low = immediate - 0x10000 if immediate & 0x8000 else immediate
                refs[(high + low) & 0xFFFFFFFF].append(origin)
            else:
                refs[(high | immediate) & 0xFFFFFFFF].append(origin)
            if rt != ra:
                pending.pop(rt, None)
            continue
        # A register written by anything else stops being half an address.
        if opcode in {15, 14, 24, 31, 11, 10, 32, 36, 40, 44, 34, 42}:
            pending.pop(rt, None)
    return refs


def entries(calls: dict[int, list[int]]) -> set[int]:
    return set(calls)


def function_of(address: int, starts: list[int]) -> int | None:
    """The known entry that most closely precedes `address`."""
    best = None
    for start in starts:
        if start <= address and (best is None or start > best):
            best = start
    return best


def reaches(calls: dict[int, list[int]], origin: int, goal: int,
            starts: list[int], depth: int = 6) -> list[int] | None:
    """A path of call sites from a function containing `origin` down to `goal`.

    Walked upwards from the goal, because the call index is keyed by target:
    who calls the goal, who calls them, and so on, until a caller lands inside
    the function `origin` belongs to.
    """
    origin_function = function_of(origin, starts)
    frontier: list[tuple[int, list[int]]] = [(goal, [])]
    seen = {goal}
    for _ in range(depth):
        nxt: list[tuple[int, list[int]]] = []
        for target, path in frontier:
            for site in calls.get(target, []):
                owner = function_of(site, starts)
                if owner is None:
                    continue
                if owner == origin_function:
                    return [site] + path
                if owner in seen:
                    continue
                seen.add(owner)
                nxt.append((owner, [site] + path))
        frontier = nxt
        if not frontier:
            break
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--base", type=lambda v: int(v, 0), required=True)
    parser.add_argument("--refs-to", type=lambda v: int(v, 0))
    parser.add_argument("--callers", type=lambda v: int(v, 0))
    parser.add_argument("--reaches", type=lambda v: int(v, 0))
    parser.add_argument("--from", dest="origin", type=lambda v: int(v, 0))
    args = parser.parse_args()

    data = args.image.read_bytes()
    calls = build_calls(data, args.base)
    starts = sorted(entries(calls))

    if args.refs_to is not None:
        sites = build_refs(data, args.base).get(args.refs_to, [])
        print(f"{len(sites)} références à 0x{args.refs_to:08x}")
        for site in sites:
            owner = function_of(site, starts)
            owned = f"  (fonction 0x{owner:08x})" if owner else ""
            print(f"  0x{site:08x}{owned}")

    if args.callers is not None:
        sites = calls.get(args.callers, [])
        print(f"{len(sites)} appelants de 0x{args.callers:08x}")
        for site in sites:
            owner = function_of(site, starts)
            owned = f"  (fonction 0x{owner:08x})" if owner else ""
            print(f"  0x{site:08x}{owned}")

    if args.reaches is not None and args.origin is not None:
        path = reaches(calls, args.origin, args.reaches, starts)
        if path is None:
            print(f"aucun chemin de 0x{args.origin:08x} vers 0x{args.reaches:08x}")
        else:
            print(f"chemin de 0x{args.origin:08x} vers 0x{args.reaches:08x} :")
            for site in path:
                print(f"  appel à 0x{site:08x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
