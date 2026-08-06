from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import fifa14_unload_completion_trace as trace


class UnloadCompletionTraceCodegenTests(unittest.TestCase):
    def test_reserved_regions_fit_owned_page(self) -> None:
        self.assertEqual(
            trace.STUB_BASE + len(trace.PROBES) * trace.STUB_STRIDE,
            trace.JOURNAL,
        )
        self.assertLessEqual(trace.JOURNAL + 4, trace.RING)
        self.assertEqual(
            trace.RING + trace.RING_COUNT * trace.RECORD_SIZE,
            trace.CAVE_END,
        )
        self.assertLessEqual(trace.CAVE_END, trace.PAGE_END)

    def test_every_stub_fits_its_slot(self) -> None:
        for probe in trace.PROBES:
            self.assertEqual(len(trace.build_stub(probe)), trace.STUB_STRIDE)

    def test_every_trampoline_resumes_after_displaced_instruction(self) -> None:
        for probe in trace.PROBES:
            image = trace.build_stub(probe)
            matches = [
                offset
                for offset in range(0, trace.STUB_STRIDE, 4)
                if image[offset : offset + 4]
                == trace.insn(
                    trace.branch(probe.stub + offset, probe.site + 4, False)
                )
            ]
            self.assertEqual(len(matches), 1)

    def test_mid_function_probe_replays_original_after_logging(self) -> None:
        probe = next(item for item in trace.PROBES if not item.prologue_mflr)
        image = trace.build_stub(probe)
        branch_offset = next(
            offset
            for offset in range(0, trace.STUB_STRIDE, 4)
            if image[offset : offset + 4]
            == trace.insn(trace.branch(probe.stub + offset, probe.site + 4, False))
        )
        self.assertEqual(image[branch_offset - 4 : branch_offset], probe.original)

    def test_range_filters_encode_both_bounds(self) -> None:
        provider = next(
            item for item in trace.PROBES if item.filter_kind == "provider-event"
        )
        image = trace.build_stub(provider)
        self.assertIn(trace.cmpwi(4, trace.PROVIDER_EVENT_FIRST).to_bytes(4, "big"), image)
        self.assertIn(trace.cmpwi(4, trace.PROVIDER_EVENT_LAST).to_bytes(4, "big"), image)

        completion = next(
            item for item in trace.PROBES if item.filter_kind == "completion-r4"
        )
        image = trace.build_stub(completion)
        self.assertIn(
            trace.cmpwi(4, trace.PROVIDER_COMPLETION_FIRST).to_bytes(4, "big"),
            image,
        )
        self.assertIn(
            trace.cmpwi(4, trace.PROVIDER_COMPLETION_LAST).to_bytes(4, "big"),
            image,
        )

    def test_event_names_cover_provider_range(self) -> None:
        self.assertEqual(
            set(trace.PROVIDER_EVENT_NAMES),
            set(range(trace.PROVIDER_EVENT_FIRST, trace.PROVIDER_EVENT_LAST + 1)),
        )

    def test_shared_provider_sites_have_exact_known_passive_patches(self) -> None:
        self.assertEqual(
            trace.EXTERNAL_PROVIDER_PATCHES,
            {
                0x82E6E1A8: bytes.fromhex("48E1A998"),
                0x8293CA98: bytes.fromhex("4934C3A8"),
                0x82974100: bytes.fromhex("49314800"),
            },
        )


if __name__ == "__main__":
    unittest.main()
