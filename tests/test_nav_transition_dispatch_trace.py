from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import fifa14_nav_transition_dispatch_trace as trace


class NavTransitionDispatchTraceCodegenTests(unittest.TestCase):
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
            image = trace.build_stub(probe).rstrip(b"\0")
            branch_word = int.from_bytes(image[-4:], "big")
            branch_site = probe.stub + len(image) - 4
            displacement = branch_word & 0x03FFFFFC
            if displacement & 0x02000000:
                displacement -= 0x04000000
            self.assertEqual(branch_site + displacement, probe.site + 4)

    def test_mid_function_sites_replay_original_after_logging(self) -> None:
        for probe in trace.PROBES:
            if probe.prologue_mflr:
                continue
            image = trace.build_stub(probe).rstrip(b"\0")
            self.assertEqual(image[-8:-4], probe.original)

    def test_atomic_slot_mask_uses_sequence_register(self) -> None:
        expected = (0x7149000F).to_bytes(4, "big")
        for probe in trace.PROBES:
            self.assertIn(expected, trace.build_stub(probe))

    def test_ion_call_preserves_vtable_register_for_displaced_load(self) -> None:
        probe = next(item for item in trace.PROBES if item.layout == "ion-call")
        image = trace.build_stub(probe).rstrip(b"\0")
        self.assertIn(trace.or_register(0, 11, 11).to_bytes(4, "big"), image)
        self.assertEqual(image[-12:-8], trace.or_register(11, 0, 0).to_bytes(4, "big"))
        self.assertEqual(image[-8:-4], probe.original)


if __name__ == "__main__":
    unittest.main()
