from __future__ import annotations

import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_dlc_loader_trace as trace


def test_verified_retail_loader_sites() -> None:
    assert [(probe.name, probe.site, probe.original.hex().upper()) for probe in trace.PROBES] == [
        ("dlc_event_callback", 0x823E9038, "7D8802A6"),
        ("dlc_automatic_item", 0x823E8D90, "7D8802A6"),
        ("load_dll_action", 0x823E8E38, "7D8802A6"),
        ("load_image_path", 0x823E8A88, "7D8802A6"),
    ]


def test_layout_and_trampolines_are_bounded() -> None:
    trace.verify_layout()
    assert trace.CAVE_END <= trace.PAGE_END
    for probe in trace.PROBES:
        image = trace.build_stub(probe)
        assert len(image) == trace.STUB_STRIDE
        assert image.startswith(probe.original)
        assert trace.patch_for(probe) != probe.original


def test_event_ring_and_path_have_room() -> None:
    assert trace.EVENT_RING_COUNT == 8
    assert trace.EVENT_RECORD_SIZE == 0x10
    assert trace.PATH_CAPACITY == 0x90
