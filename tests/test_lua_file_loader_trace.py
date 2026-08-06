from __future__ import annotations

import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import fifa14_lua_file_loader_trace as trace


def test_verified_registration_addresses_and_sites() -> None:
    assert [(p.name, p.site, p.original.hex().upper(), p.path_register) for p in trace.PROBES] == [
        ("fileexists_path", 0x83728AF4, "817F0004", 3),
        ("loadfileasync_path", 0x83729404, "817E0000", 3),
        ("loadfileasync_request", 0x83729200, "7D8802A6", 6),
    ]


def test_stub_layout_and_return_branches() -> None:
    trace.verify_layout()
    for probe in trace.PROBES:
        image = trace.build_stub(probe)
        assert len(image) == trace.STUB_STRIDE
        assert probe.original in image
        assert image.rstrip(b"\0")
        assert trace.STUB_BASE <= probe.stub < trace.JOURNAL
        assert trace.patch_for(probe) != probe.original


def test_journal_is_bounded_to_shared_diagnostic_page() -> None:
    assert trace.PATH_CAPACITY == 0x60
    assert trace.CAVE_END <= trace.PAGE_END
