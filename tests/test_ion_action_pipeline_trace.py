from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import fifa14_ion_action_pipeline_trace as trace


class IonActionPipelineTraceCodegenTests(unittest.TestCase):
    def test_reserved_regions_do_not_overlap(self) -> None:
        self.assertEqual(
            trace.STUB_BASE + len(trace.PROBES) * trace.STUB_STRIDE,
            trace.JOURNAL,
        )
        self.assertLessEqual(trace.JOURNAL + 4, trace.RING)
        self.assertEqual(
            trace.RING + trace.RING_COUNT * trace.RECORD_SIZE,
            trace.CAVE_END,
        )
        self.assertLessEqual(trace.CAVE_END, trace.NEXT_KNOWN_CAVE)

    def test_every_stub_fits_its_slot(self) -> None:
        for probe in trace.PROBES:
            self.assertEqual(len(trace.build_stub(probe)), trace.STUB_STRIDE)

    def test_trampolines_resume_after_displaced_instruction(self) -> None:
        for probe in trace.PROBES:
            image = trace.build_stub(probe).rstrip(b"\0")
            branch_word = int.from_bytes(image[-4:], "big")
            branch_site = probe.stub + len(image) - 4
            displacement = branch_word & 0x03FFFFFC
            if displacement & 0x02000000:
                displacement -= 0x04000000
            self.assertEqual(branch_site + displacement, probe.site + 4)


if __name__ == "__main__":
    unittest.main()
