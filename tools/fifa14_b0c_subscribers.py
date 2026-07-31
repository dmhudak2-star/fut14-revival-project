#!/usr/bin/env python3
"""Enumerate the live subscribers reached by the FUT B0C connection callback."""

from __future__ import annotations

import argparse

from fifa14_plain_send_hook import Xbdm, verify_module


LISTENER = 0xBD2DC740
SIGNAL_OFFSETS = (0x6F8, 0x778, 0x7F8)
NETWORK_CALLBACK = 0x8251A560
CONNECTED_TRANSITION = 0x825927A8


def u32(client: Xbdm, address: int) -> int:
    return int.from_bytes(client.read(address, 4), "big")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--listener", type=lambda value: int(value, 0), default=LISTENER)
    args = parser.parse_args()

    client = Xbdm(args.host)
    try:
        verify_module(client)
        network_receivers: list[int] = []
        for signal_offset in SIGNAL_OFFSETS:
            signal = args.listener + signal_offset
            begin = u32(client, signal + 4)
            end = u32(client, signal + 8)
            if end < begin or (end - begin) % 4:
                print(
                    f"signal_{signal_offset:03X}: invalid "
                    f"begin=0x{begin:08X} end=0x{end:08X}"
                )
                continue
            count = (end - begin) // 4
            print(
                f"signal_{signal_offset:03X}: begin=0x{begin:08X} "
                f"end=0x{end:08X} count={count}"
            )
            for index in range(count):
                slot = begin + index * 4
                receiver = u32(client, slot)
                try:
                    vtable = u32(client, receiver)
                    callback = u32(client, vtable + 4)
                    prefix = client.read(receiver, 0x10).hex().upper()
                    print(
                        f"  [{index:02d}] slot=0x{slot:08X} "
                        f"receiver=0x{receiver:08X} vtable=0x{vtable:08X} "
                        f"callback=0x{callback:08X} words={prefix}"
                    )
                    if callback == NETWORK_CALLBACK:
                        network_receivers.append(receiver)
                except Exception as error:
                    print(
                        f"  [{index:02d}] slot=0x{slot:08X} "
                        f"receiver=0x{receiver:08X} unreadable={error}"
                    )
        for receiver in network_receivers:
            observers = u32(client, receiver - 0x48)
            count = u32(client, receiver - 0x44)
            state = u32(client, receiver + 0x974)
            print(
                f"network_listener=0x{receiver:08X}: "
                f"observers=0x{observers:08X} count={count} state={state}"
            )
            if count > 0x100:
                print("  observer count is implausible; refusing to walk it")
                continue
            for index in range(count):
                slot = observers + index * 4
                observer = u32(client, slot)
                try:
                    vtable = u32(client, observer)
                    callback = u32(client, vtable)
                    prefix = client.read(observer, 0x20).hex().upper()
                    print(
                        f"  observer[{index:02d}] slot=0x{slot:08X} "
                        f"object=0x{observer:08X} vtable=0x{vtable:08X} "
                        f"callback=0x{callback:08X} words={prefix}"
                    )
                    if callback == CONNECTED_TRANSITION:
                        owner = observer - 0x18
                        owner_vtable = u32(client, owner)
                        target = u32(client, owner_vtable + 0x28)
                        predicate_80 = u32(client, owner_vtable + 0x80)
                        predicate_84 = u32(client, owner_vtable + 0x84)
                        owner_prefix = client.read(owner, 0x70).hex().upper()
                        global_gate = client.read(0x83D70235, 1)[0]
                        print(
                            f"    connected_owner=0x{owner:08X} "
                            f"vtable=0x{owner_vtable:08X} "
                            f"transition_target=0x{target:08X} "
                            f"predicate80=0x{predicate_80:08X} "
                            f"predicate84=0x{predicate_84:08X} "
                            f"byte31=0x{owner_prefix[0x31 * 2:0x31 * 2 + 2]} "
                            f"global_gate=0x{global_gate:02X}"
                        )
                        print(f"    owner_words={owner_prefix}")
                except Exception as error:
                    print(
                        f"  observer[{index:02d}] slot=0x{slot:08X} "
                        f"object=0x{observer:08X} unreadable={error}"
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
