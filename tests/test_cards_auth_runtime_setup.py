from __future__ import annotations

import sys
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_cards_auth_runtime_setup as setup


def test_module_state_accepts_supported_cardsdll() -> None:
    assert setup.module_state(
        ['name="powdllzf.xex.dll" base=0x89700000 size=0x00100000']
    ) == "supported"


def test_module_state_is_case_insensitive() -> None:
    assert setup.module_state(
        ['name="POWDLLZF.XEX.DLL" base=0x89700000 size=0x00100000']
    ) == "supported"


def test_module_state_reports_absent() -> None:
    assert setup.module_state(['name="default.xex" base=0x82000000']) == "absent"


def test_module_state_rejects_unexpected_base() -> None:
    with pytest.raises(RuntimeError, match="Unexpected powdllzf"):
        setup.module_state(
            ['name="powdllzf.xex.dll" base=0x89800000 size=0x00100000']
        )


def test_patch_site_state_accepts_original_tu3_instructions() -> None:
    assert setup.patch_site_state(
        bytes.fromhex("7C7E1B79 418200EC"),
        bytes.fromhex("7C7E1B79 41820098"),
        bytes.fromhex("7D8802A6"),
    ) == "ready"


def test_patch_site_state_rejects_transient_modload_bytes() -> None:
    state = setup.patch_site_state(
        bytes.fromhex("F3804163461B4C4B"),
        bytes.fromhex("EBBDF6872210D64A"),
        bytes.fromhex("7D8802A6"),
    )
    assert state.startswith("session=F3804163461B4C4B")
