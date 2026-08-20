"""The connect redirect stub must fit the cave it is written into.

It is at 252 bytes of 256. That is four bytes of headroom, and the next port
added to `LOCAL_PLAINTEXT_PORTS` costs eight -- so the failure this pins is one
edit away, and it is a silent one.

What overflowing does is worse than not fitting. `CONNECT_LOG` starts where the
cave ends, and the launcher zeroes it *before* writing the stub, so an
oversized stub is published intact and looks fine. Then the stub journals a
connect into CONNECT_LOG at runtime and overwrites its own tail, on a console,
mid-session. There is no version of that which reads as "the stub is too long".
"""

from __future__ import annotations

import ipaddress
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_connect_bypass as CB  # noqa: E402
import fifa14_connect_redirect as CR  # noqa: E402

CAVE = CB.CONNECT_LOG - CB.CONNECT_STUB


def build(ip: str = "192.168.1.40") -> bytes:
    return CR.build_stub(int(ipaddress.IPv4Address(ip)))


def test_the_stub_fits_between_itself_and_the_journal() -> None:
    stub = build()
    assert len(stub) <= CAVE, (
        f"the connect stub is {len(stub)} bytes and its cave is {CAVE}; "
        f"it would overwrite CONNECT_LOG at 0x{CB.CONNECT_LOG:08X}"
    )


def test_every_variant_fits_too() -> None:
    # The legacy and pre-plaintext images are longer, and they are still built
    # when an old live hook has to be identified before it is migrated.
    for kwargs in (
        {"legacy_global_lr": True},
        {"unsecure_socket": False},
    ):
        stub = CR.build_stub(int(ipaddress.IPv4Address("192.168.1.40")), **kwargs)
        assert len(stub) <= CAVE, f"{kwargs} builds {len(stub)} bytes into {CAVE}"


def test_the_easfc_ports_are_redirected() -> None:
    # 8094 is the EAS FC Blaze session, 8080 its catalogue. Both were outside
    # the filter, so the module's connects passed through the hook untouched
    # and went out to the internet -- which is the "EAS FC non connecté"
    # banner, with nothing in our journal because nothing reached us.
    assert CR.EASFC_SESSION_PORT in CR.LOCAL_PLAINTEXT_PORTS
    assert CR.EASFC_CATALOGUE_PORT in CR.LOCAL_PLAINTEXT_PORTS


def test_each_port_costs_two_instructions_and_the_budget_is_known() -> None:
    # Stated as arithmetic rather than as a comment, so the headroom is a fact
    # the suite carries: one comparison and one branch per port.
    without = len(build())
    original = CR.LOCAL_PLAINTEXT_PORTS
    try:
        CR.LOCAL_PLAINTEXT_PORTS = (*original, 12345)
        assert len(build()) == without + 8
    finally:
        CR.LOCAL_PLAINTEXT_PORTS = original
    assert CAVE - without == 4, (
        f"headroom changed to {CAVE - without} bytes; if it grew, say so in "
        "the docstring, and if it shrank to zero the next port cannot be added"
    )
