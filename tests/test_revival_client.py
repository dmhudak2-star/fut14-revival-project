"""The console-side client, on the parts that do not need a console.

`tools/revival_client.py` is what a player runs when the server is somewhere
else -- a VPS, or a phone on the same Wi-Fi. Most of it drives XBDM and cannot
be exercised here. What can be, and is worth pinning:

  * it resolves the server address itself, because the patchers write an IP
    into the title's memory and the EAS FC strings have no room for a name;
  * the account reset is a request rather than a file, and a server that does
    not know the route is not a failed run;
  * `POST /revival/reset` really does put the account store back to the state
    a freshly started server has -- which is the whole thing `tools/fut.sh`
    got by clearing a file and restarting.
"""

from __future__ import annotations

import http.client
import http.server
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import revival_client  # noqa: E402

SERVER_PATH = ROOT / "server" / "fifa14_blaze_server.py"
SPEC = importlib.util.spec_from_file_location("fifa14_blaze_server", SERVER_PATH)
assert SPEC is not None and SPEC.loader is not None
SERVER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SERVER
SPEC.loader.exec_module(SERVER)


class StubServer:
    """An HTTP server that records what it was asked, and answers `status`."""

    def __init__(self, status: int = 200) -> None:
        self.seen: list[tuple[str, str]] = []
        recorder = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def do_POST(self) -> None:
                recorder.seen.append((self.command, self.path))
                body = b"session reinitialisee\n"
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if status == 200:
                    self.wfile.write(body)

        self.http = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.http.server_address[1]
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    def __enter__(self) -> "StubServer":
        self.thread.start()
        return self

    def __exit__(self, *_exception: object) -> None:
        self.http.shutdown()
        self.http.server_close()


class ResetTest(unittest.TestCase):
    def test_it_posts_the_reset_before_the_launch(self) -> None:
        with StubServer() as stub:
            revival_client.reset_account(f"http://127.0.0.1:{stub.port}")
        self.assertEqual(stub.seen, [("POST", "/revival/reset")])

    def test_an_older_server_that_has_no_such_route_is_not_a_failed_run(self) -> None:
        # A 404 costs a stale FirstTimeFlag, not a launch. Raising here would
        # stop a player whose only problem is a server from before this route.
        with StubServer(status=404) as stub:
            revival_client.reset_account(f"http://127.0.0.1:{stub.port}")
        self.assertEqual(stub.seen, [("POST", "/revival/reset")])

    def test_an_unreachable_server_is_reported_and_survived(self) -> None:
        # Port 9 on localhost: discard, and nothing listening in a test runner.
        revival_client.reset_account("http://127.0.0.1:9")


class AddressTest(unittest.TestCase):
    def test_a_name_that_does_not_resolve_stops_before_touching_the_console(self) -> None:
        code = revival_client.main([
            "--console", "192.0.2.25",
            "--server", "server.invalid.",
        ])
        self.assertEqual(code, 2)


class ServerRouteTest(unittest.TestCase):
    def test_reset_restores_the_state_a_fresh_server_has(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            accounts = SERVER.AccountStores(Path(temp) / "account.json")
            store = accounts.get(0)
            # What a title that has been through first-login leaves behind.
            store.save_setting("FirstTimeFlag", "1")
            store.save_identity(0x123456789, "SomebodyElse")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal, accounts
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("POST", "/revival/reset")
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                client.close()
            finally:
                identity.stop()

            self.assertEqual(store.load_setting("FirstTimeFlag"), "0")
            self.assertEqual(store.load_identity(), (1_000_001, "OfflineFUT"))
            # And it is on disk, so it survives the restart it stands in for.
            saved = json.loads((Path(temp) / "account.json").read_text())
            self.assertEqual(saved["user_settings"]["FirstTimeFlag"], "0")

    def test_a_get_does_not_reset_anything(self) -> None:
        # The route is a POST. A crawler, a browser tab or a health check
        # walking the server must not wipe a session out from under a player.
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            accounts = SERVER.AccountStores(Path(temp) / "account.json")
            store = accounts.get(0)
            store.save_setting("FirstTimeFlag", "1")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal, accounts
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("GET", "/revival/reset")
                client.getresponse().read()
                client.close()
            finally:
                identity.stop()
            self.assertEqual(store.load_setting("FirstTimeFlag"), "1")


if __name__ == "__main__":
    unittest.main()
