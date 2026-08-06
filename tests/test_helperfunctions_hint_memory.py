from __future__ import annotations

import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_tu3_helperfunctions_runtime_patch as patch


def test_a_remembered_address_survives_a_round_trip(tmp_path: Path) -> None:
    store = tmp_path / "nested" / "apt.json"
    patch.remember_hint(store, 0xBDD78000)
    assert patch.remembered_hint(store) == 0xBDD78000


def test_a_missing_store_yields_no_hint(tmp_path: Path) -> None:
    assert patch.remembered_hint(tmp_path / "absent.json") is None


def test_a_corrupt_store_yields_no_hint_rather_than_raising(tmp_path: Path) -> None:
    # A truncated write must degrade to the built-in hint, never break a cycle.
    store = tmp_path / "apt.json"
    store.write_text('{"address": ')
    assert patch.remembered_hint(store) is None
    store.write_text('{"address": "0xBDD78000"}')
    assert patch.remembered_hint(store) is None


def test_remembering_never_raises_on_an_unwritable_path(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    patch.remember_hint(blocker / "apt.json", 0xBDD78000)


def test_the_window_keeps_only_the_span_around_the_hint() -> None:
    regions = [(0xB0000000, 0x10000000, 4), (0xC8000000, 0x1000, 4)]
    clipped = patch.clip_to_window(regions, 0xBDD78000, 0x00400000)
    assert clipped == [(0xBDB78000, 0x00400000, 4)]


def test_the_window_drops_regions_it_does_not_reach() -> None:
    regions = [(0xC8000000, 0x1000, 4)]
    assert patch.clip_to_window(regions, 0xBDD78000, 0x00400000) == []
