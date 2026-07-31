#!/usr/bin/env python3
"""Journal FIFA's Xbox XNet resolution path without breakpoints."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from fifa14_plain_send_hook import Xbdm, branch, insn, verify_module


LOG_BASE = 0x83C8EF00
LOG_STRIDE = 0x30


@dataclass(frozen=True)
class Site:
    name: str
    wrapper: int
    original: bytes
    target: int
    stub: int


SITES = (
    Site(
        "XNetServerToInAddr",
        0x83995090,
        bytes.fromhex("482EE8C4"),
        0x81740FC0,
        0x83C8EB00,
    ),
    Site(
        "XNetConnect",
        0x839950B0,
        bytes.fromhex("482EE8C4"),
        0x81741100,
        0x83C8EBC0,
    ),
    Site(
        "XNetGetConnectStatus",
        0x839950C0,
        bytes.fromhex("482EE8C4"),
        0x81741128,
        0x83C8EC80,
    ),
    Site(
        "XNetDnsLookup",
        0x839950D8,
        bytes.fromhex("482EE8BC"),
        0x81741150,
        0x83C8ED40,
    ),
    Site(
        "XNetQosServiceLookup",
        0x83995180,
        bytes.fromhex("482EE854"),
        0x81741238,
        0x83C8EE00,
    ),
)


def addi(rt: int, ra: int, immediate: int) -> int:
    return 0x38000000 | (rt << 21) | (ra << 16) | (immediate & 0xFFFF)


def addis(rt: int, ra: int, immediate: int) -> int:
    return 0x3C000000 | (rt << 21) | (ra << 16) | (immediate & 0xFFFF)


def lwz(rt: int, ra: int, displacement: int) -> int:
    return 0x80000000 | (rt << 21) | (ra << 16) | (displacement & 0xFFFF)


def stw(rs: int, ra: int, displacement: int) -> int:
    return 0x90000000 | (rs << 21) | (ra << 16) | (displacement & 0xFFFF)


def build_stub(site: Site, index: int) -> bytes:
    log = LOG_BASE + index * LOG_STRIDE
    words = [
        addis(12, 0, (log + 0x8000) >> 16),
        addi(12, 12, log & 0xFFFF),
        lwz(11, 12, 0),
        addi(11, 11, 1),
        stw(11, 12, 0),
    ]
    for register in range(3, 11):
        words.append(stw(register, 12, 4 + (register - 3) * 4))
    high = (site.target + 0x8000) >> 16
    words.extend(
        [
            addis(11, 0, high),
            addi(11, 11, site.target & 0xFFFF),
            0x7D6903A6,  # mtctr r11
            0x4E800420,  # bctr, preserving the caller's LR
        ]
    )
    return b"".join(insn(word) for word in words)


STUBS = tuple(build_stub(site, index) for index, site in enumerate(SITES))
PATCHES = tuple(
    insn(branch(site.wrapper, site.stub, link=False)) for site in SITES
)


def state(client: Xbdm, index: int) -> str:
    value = client.read(SITES[index].wrapper, 4)
    if value == SITES[index].original:
        return "original"
    if value == PATCHES[index]:
        return "hooked"
    return f"unexpected:{value.hex().upper()}"


def print_logs(client: Xbdm) -> None:
    for index, site in enumerate(SITES):
        data = client.read(LOG_BASE + index * LOG_STRIDE, LOG_STRIDE)
        values = [
            int.from_bytes(data[offset : offset + 4], "big")
            for offset in range(0, 0x24, 4)
        ]
        registers = " ".join(
            f"r{register}=0x{values[register - 2]:08X}"
            for register in range(3, 11)
        )
        print(f"{site.name}: calls={values[0]} {registers}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("action", choices=("status", "apply", "restore"))
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        states = [state(client, index) for index in range(len(SITES))]
        print(
            "Sites: "
            + ", ".join(
                f"{site.name}={current}"
                for site, current in zip(SITES, states)
            )
        )
        if args.action == "status":
            print_logs(client)
            return 0

        if args.action == "apply":
            if all(current == "hooked" for current in states):
                print_logs(client)
                return 0
            if any(current != "original" for current in states):
                raise RuntimeError("Refusing to overwrite unexpected wrapper")
            for site, stub in zip(SITES, STUBS):
                cave = client.read(site.stub, len(stub))
                if cave not in (bytes(len(stub)), stub):
                    raise RuntimeError(
                        f"Code cave 0x{site.stub:08X} is not empty"
                    )
            client.write(LOG_BASE, bytes(LOG_STRIDE * len(SITES)))
            for site, stub in zip(SITES, STUBS):
                client.write(site.stub, stub)
                if client.read(site.stub, len(stub)) != stub:
                    raise RuntimeError(
                        f"Stub verification failed at 0x{site.stub:08X}"
                    )
            published: list[int] = []
            try:
                for index, site in enumerate(SITES):
                    client.write(site.wrapper, PATCHES[index])
                    published.append(index)
                if any(
                    state(client, index) != "hooked"
                    for index in range(len(SITES))
                ):
                    raise RuntimeError("XNet journal publication failed")
            except Exception:
                for index in reversed(published):
                    try:
                        client.write(SITES[index].wrapper, SITES[index].original)
                    except Exception:
                        pass
                raise
            print("Verified: multi-stage XNet journal active.")
            return 0

        for index, site in enumerate(SITES):
            current = states[index]
            if current == "hooked":
                client.write(site.wrapper, site.original)
            elif current != "original":
                raise RuntimeError(
                    f"Refusing to restore unexpected {site.name} wrapper"
                )
        if any(
            state(client, index) != "original"
            for index in range(len(SITES))
        ):
            raise RuntimeError("XNet multi-journal restore failed")
        print("Verified: XNet wrappers restored.")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}")
        raise SystemExit(1)
