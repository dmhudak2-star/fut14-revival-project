#!/usr/bin/env python3
"""Ask a hung title which instruction it is sitting on.

Four documents were served to the console to resume a cup and all four froze
it, each guess costing a freeze and a relaunch. Guessing stops here.

The freeze is a **hang, not a crash**: XBDM keeps answering, `xbeinfo running`
still names FIFA, and the frontend simply stops. So a thread is stuck, and
stock XBDM can say where -- `threads` enumerates them and `getcontext` reads
the instruction address of each. That turns "which member name does the parser
want" into "what is the code at 0x89xxxxxx doing", which is a question the
disassembler can answer offline.

Run it against a healthy title first: the point is to know the tool works
before spending a freeze on it.

    tools/fifa14_where_is_it_stuck.py 192.168.1.25
    tools/fifa14_where_is_it_stuck.py 192.168.1.25 --repeat 3

`--repeat` samples more than once. A thread whose instruction address does not
move between samples is stuck; one that moves is merely busy, which matters
because a frozen frontend still has audio and render threads running.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fifa14_plain_send_hook import Xbdm  # noqa: E402

# Where the modules live on the supported build. `default.xex` is documented in
# README.md ("Supported build"); CardsDLL is only mapped once FUT is entered,
# which is exactly when this matters.
MODULES = (
    ("default.xex", 0x82000000, 0x023EC400),
    ("CardsDLL", 0x89000000, 0x00700000),
    ("powdllzf", 0x89700000, 0x00100000),
)


def module_of(address: int) -> str:
    for name, base, size in MODULES:
        if base <= address < base + size:
            return f"{name}+0x{address - base:06x}"
    if 0x80000000 <= address < 0x82000000:
        return "kernel/XAM"
    return "heap or unmapped"


def thread_ids(client: Xbdm) -> list[int]:
    ids: list[int] = []
    for line in client.multiline("threads"):
        text = line.strip()
        if not text or text.startswith("2") and "follows" in text:
            continue
        try:
            # XBDM prints thread ids signed and only accepts them unsigned:
            # `threads` answers -83886068 and `getcontext thread=-83886068`
            # comes back "400- missing thread", while 4211081228 works.
            ids.append(int(text) & 0xFFFFFFFF)
        except ValueError:
            continue
    return ids


def context(client: Xbdm, thread: int) -> dict[str, int]:
    """One thread's registers. `iar` is where it is executing, `gpr1` its stack."""
    values: dict[str, int] = {}
    try:
        lines = client.multiline(f"getcontext thread={thread} control int")
    except Exception:
        return values
    for line in lines:
        for pair in line.replace("\r", "").split():
            name, _, raw = pair.partition("=")
            if not raw:
                continue
            # Two formats in one reply: `0x` for the control registers and
            # `0q` followed by sixty-four bits for the general-purpose ones.
            text = raw.lower()
            try:
                if text.startswith("0q"):
                    values[name.lower()] = int(text[2:], 16) & 0xFFFFFFFF
                elif text.startswith("0x"):
                    values[name.lower()] = int(text, 16)
                else:
                    values[name.lower()] = int(text, 10)
            except ValueError:
                continue
    return values


def unwind(client: Xbdm, stack: int, depth: int = 12) -> list[int]:
    """Walk the PowerPC back chain and collect the saved return addresses.

    Every frame stores its caller's stack pointer at offset 0 and the return
    address the caller will branch to at offset 4, so the chain reads without
    any symbol information. It is what turns one instruction address into the
    path that reached it.
    """
    addresses: list[int] = []
    frame = stack
    for _ in range(depth):
        if not frame or frame & 3 or not 0x10000000 <= frame < 0xF0000000:
            break
        try:
            block = client.read(frame, 8)
        except Exception:
            break
        if len(block) < 8:
            break
        nxt = int.from_bytes(block[0:4], "big")
        link = int.from_bytes(block[4:8], "big")
        if link:
            addresses.append(link)
        if nxt <= frame:
            break
        frame = nxt
    return addresses


def sample(client: Xbdm) -> dict[int, dict[str, int]]:
    return {thread: context(client, thread) for thread in thread_ids(client)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--depth", type=int, default=10)
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        samples = []
        for index in range(max(1, args.repeat)):
            if index:
                time.sleep(args.interval)
            samples.append(sample(client))

        threads = sorted({thread for shot in samples for thread in shot})
        if not threads:
            print("aucun thread listé -- XBDM n'a rien renvoyé pour `threads`")
            return 1

        # Nearly every thread sits in a kernel wait, and at the moment of the
        # freeze the interesting one will too: a frontend that hangs is a
        # frontend waiting on something that never comes. So the instruction
        # address says almost nothing and the **stack** says everything --
        # whoever called into that wait is still on it.
        print(f"{len(threads)} threads, {len(samples)} échantillons\n")
        reported = 0
        for thread in threads:
            seen = [shot.get(thread, {}) for shot in samples]
            addresses = [v.get("iar") for v in seen if v.get("iar")]
            if not addresses:
                continue
            values = seen[0]
            stack = values.get("gpr1")
            chain = unwind(client, stack, args.depth) if stack else []
            titled = [a for a in chain if "+" in module_of(a)]
            if not titled and "+" not in module_of(addresses[0]):
                continue
            reported += 1
            moving = len(set(addresses)) > 1
            print(f"  thread {thread}  {'avance' if moving else 'IMMOBILE'}")
            print(f"      iar=0x{addresses[0]:08x}  {module_of(addresses[0])}")
            link = values.get("lr")
            if link:
                print(f"      lr =0x{link:08x}  {module_of(link)}")
            for register in ("gpr3", "gpr4", "gpr5", "gpr6"):
                value = values.get(register)
                if value and "+" in module_of(value):
                    print(f"      {register}=0x{value:08x}  {module_of(value)}")
            if titled:
                print("      pile :")
                for address in titled[:10]:
                    print(f"        0x{address:08x}  {module_of(address)}")
            if moving:
                print("      " + " -> ".join(f"0x{a:08x}" for a in addresses))
            print()
        if not reported:
            print("aucun thread ne traverse le code du titre -- "
                  "élargir --depth, ou le titre n'est pas chargé")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
