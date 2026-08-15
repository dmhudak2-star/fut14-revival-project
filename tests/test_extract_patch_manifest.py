"""The plugin builds from this manifest, so a silent drift here ships a broken
plugin. These pin the shape and the invariants, not the exact bytes -- the
bytes come from the patch modules and are allowed to move with them."""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import extract_patch_manifest as M  # noqa: E402


def test_the_manifest_has_all_three_stages() -> None:
    m = M.build("192.168.1.40", 10041, 18080)
    assert set(m) >= {"stage1_launch", "stage2_easfc", "stage3_tu3", "server", "build"}
    assert m["build"]["default_xex_timestamp"] == "0x534C8977"


def test_every_launch_hook_names_the_bytes_it_expects() -> None:
    # A hook that writes without checking the original bytes corrupts a wrong
    # build. Every one must carry both.
    m = M.build("192.168.1.40", 10041, 18080)
    sites = m["stage1_launch"]["sites"]
    assert sites, "no launch hooks emitted"
    for site in sites:
        assert site["expect"] and site["write"]
        assert len(bytes.fromhex(site["expect"])) >= 4
        assert isinstance(site["address"], int)


def test_the_ip_actually_changes_the_manifest() -> None:
    # If the server address did not flow into the bytes, a plugin built for one
    # server would talk to another. The connect redirect stub carries it.
    a = M.build("192.168.1.40", 10041, 18080)
    b = M.build("10.20.30.40", 10041, 18080)

    def redirect(man):
        return next(c["bytes"] for c in man["stage1_launch"]["caves"]
                    if c["name"] == "connect_redirect_stub")

    assert redirect(a) != redirect(b)
    # And the EAS FC strings carry it too.
    assert a["stage2_easfc"]["strings"] != b["stage2_easfc"]["strings"]


def test_a_max_length_ipv4_fits_both_easfc_slots() -> None:
    # In-place rewrite: a replacement longer than the original cannot be
    # written. A maximal IPv4 is the worst case and must still fit, or a plugin
    # that resolves to one would be stuck.
    m = M.build("255.255.255.255", 10041, 18080)
    for s in m["stage2_easfc"]["strings"]:
        assert s["fits"], f"{s['name']} overruns: {len(s['write'])} > {s['budget']}"


def test_the_tu3_branches_carry_their_context_guards() -> None:
    # The APT is pattern-located, so each branch write is guarded by the bytes
    # around it. Missing guards would let the plugin write into the wrong place.
    m = M.build("192.168.1.40", 10041, 18080)
    tu3 = m["stage3_tu3"]
    assert len(bytes.fromhex(tu3["signature"])) == 48
    assert len(tu3["branches"]) == 3
    for branch in tu3["branches"]:
        assert branch["context_before"] and branch["context_after"]
        assert len(bytes.fromhex(branch["expect"])) == 6
        assert len(bytes.fromhex(branch["write"])) == 6
