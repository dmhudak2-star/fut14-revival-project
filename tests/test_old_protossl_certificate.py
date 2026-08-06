from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "generate_old_protossl_certificate.py"
SPEC = importlib.util.spec_from_file_location("old_protossl_certificate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CertificatePatchTests(unittest.TestCase):
    def test_only_second_signature_algorithm_oid_is_changed(self) -> None:
        original = (
            b"prefix"
            + MODULE.MD5_WITH_RSA_OID
            + b"middle"
            + MODULE.MD5_WITH_RSA_OID
            + b"suffix"
        )
        patched = MODULE.patch_outer_signature_oid(original)
        self.assertEqual(patched.count(MODULE.MD5_WITH_RSA_OID), 1)
        self.assertEqual(patched.count(MODULE.RSA_ENCRYPTION_OID), 1)
        self.assertEqual(
            patched.find(MODULE.MD5_WITH_RSA_OID),
            original.find(MODULE.MD5_WITH_RSA_OID),
        )

    def test_refuses_ambiguous_certificate(self) -> None:
        with self.assertRaises(RuntimeError):
            MODULE.patch_outer_signature_oid(MODULE.MD5_WITH_RSA_OID)


if __name__ == "__main__":
    unittest.main()
