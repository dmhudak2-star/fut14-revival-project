from __future__ import annotations

import ipaddress
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import fifa14_connect_redirect as redirect


class ConnectRedirectCodegenTests(unittest.TestCase):
    def test_code_caves_do_not_overlap(self) -> None:
        stub = redirect.build_stub(
            int(ipaddress.IPv4Address("192.0.2.35"))
        )
        self.assertLessEqual(
            len(stub),
            redirect.CONNECT_LOG - redirect.CONNECT_STUB,
        )
        self.assertLessEqual(len(redirect.SOCKET_SECURITY_STUB_BYTES), 0x100)
        self.assertLessEqual(len(redirect.CONNECT_RESULT_STUB_BYTES), 0x100)

    def test_native_xins_control_and_wsa_audit_are_emitted(self) -> None:
        helper = redirect.SOCKET_SECURITY_STUB_BYTES
        self.assertIn(bytes.fromhex("3C80786960846E73"), helper)
        self.assertIn(bytes.fromhex("3D6082D7396BA370"), helper)
        self.assertIn(bytes.fromhex("3D608174396BFE78"), helper)
        self.assertIn(bytes.fromhex("3D608174396BFE70"), helper)
        self.assertEqual(helper.count(bytes.fromhex("4E800421")), 3)

    def test_redirect_is_narrow_and_calls_security_helper_once(self) -> None:
        local_ip = int(ipaddress.IPv4Address("192.0.2.35"))
        secured = redirect.build_stub(local_ip)
        legacy = redirect.build_stub(local_ip, unsecure_socket=False)
        helper_call = bytes.fromhex("3D6083C9396BE7807D6903A64E800421")
        self.assertEqual(secured.count(helper_call), 1)
        self.assertNotIn(helper_call, legacy)
        self.assertGreater(len(secured), len(legacy))

    def test_identity_http_port_uses_the_plaintext_socket_path(self) -> None:
        secured = redirect.build_stub(
            int(ipaddress.IPv4Address("192.0.2.35"))
        )
        identity_compare = (
            0x28000000
            | (10 << 16)
            | redirect.IDENTITY_HTTP_PORT
        ).to_bytes(4, "big")
        self.assertIn(identity_compare, secured)
        # The list is pinned because the redirect is meant to be *narrow*:
        # every port here is one the title's traffic gets pulled off the
        # internet for, so adding one is a decision, not a detail.
        #
        # 8094 and 8080 are EAS FC's session and catalogue. They were added on
        # 20 August after the endpoint strings were read back from a running
        # title -- correctly rewritten, still in place -- with the server never
        # having seen a single connection from that module. Redirecting by port
        # does not care which endpoint the module kept.
        self.assertEqual(
            redirect.LOCAL_PLAINTEXT_PORTS,
            (10041, 42124, 42126, 42127, 18080, 8094, 8080),
        )


if __name__ == "__main__":
    unittest.main()
