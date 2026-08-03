from __future__ import annotations

import http.client
import importlib.util
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "server" / "fifa14_blaze_server.py"
SPEC = importlib.util.spec_from_file_location("fifa14_blaze_server", SERVER_PATH)
assert SPEC is not None and SPEC.loader is not None
SERVER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SERVER
SPEC.loader.exec_module(SERVER)

from blaze_tdf import BINARY, INTEGER, LIST, MAP, STRING, STRUCT, Field, decode_frame, encode_fields, encode_frame


def request(component: int, command: int, fields: list[Field] | None = None) -> bytes:
    return encode_frame(
        component,
        command,
        0,
        0,
        0x12345,
        encode_fields(fields or []),
    )


def by_label(decoded: dict, label: str) -> Field:
    value = SERVER.find_field(decoded["fields"], label)
    if value is None:
        raise AssertionError(f"Missing {label}")
    return value


class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal = SERVER.Journal(Path(self.temp.name) / "journal.jsonl")
        self.protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, self.journal)
        self.state = SERVER.ClientState(1, ("192.0.2.25", 12345), 10041)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_redirector_points_to_local_core(self) -> None:
        response = self.protocol.handle(request(5, 1), self.state)[0]
        decoded = decode_frame(response)
        self.assertEqual(decoded["message_type"], 1)
        self.assertEqual(decoded["message_number"], 0x12345)
        self.assertEqual(by_label(decoded, "HOST").value, "192.0.2.35")
        self.assertEqual(by_label(decoded, "PORT").value, 10041)
        self.assertEqual(by_label(decoded, "SECU").value, 0)

    def test_preauth_advertises_fifa_xbox_and_cardhouse(self) -> None:
        response = self.protocol.handle(request(9, 7), self.state)[0]
        decoded = decode_frame(response)
        self.assertEqual(by_label(decoded, "EEFA").value, 1)
        self.assertEqual(by_label(decoded, "ESRC").value, "fifa-2014-xbl2")
        self.assertEqual(by_label(decoded, "INST").value, "fifa-2014-xbl2")
        self.assertEqual(by_label(decoded, "PILD").value, "fifa-2014-xbl2")
        self.assertEqual(by_label(decoded, "PLAT").value, "xbox360")
        item_type, component_ids = by_label(decoded, "CIDS").value
        self.assertEqual(item_type, INTEGER)
        self.assertIn(2148, component_ids)
        self.assertIn(35, component_ids)
        outer_config = by_label(decoded, "CONF")
        inner_config = SERVER.find_field(outer_config.value, "CONF")
        self.assertIsNotNone(inner_config)
        self.assertEqual(inner_config.type, MAP)
        config = dict(inner_config.value[2])
        self.assertEqual(config["nucleusConnect"], "http://192.0.2.35:18080")
        self.assertEqual(config["xblTokenUrn"], "http://accounts.ea.com")

    def test_identity_params_point_to_local_redirect(self) -> None:
        response = self.protocol.handle(
            request(9, 1, [Field("CFID", STRING, "IdentityParams")]),
            self.state,
        )[0]
        decoded = decode_frame(response)
        config = by_label(decoded, "CONF")
        self.assertEqual(config.type, MAP)
        values = dict(config.value[2])
        self.assertEqual(values["client_id"], "fifa14-xbox360-offline")
        self.assertEqual(
            values["redirect_uri"],
            "http://192.0.2.35:18080/connect/redirect",
        )

    def test_osdk_client_disables_only_dead_asset_refresh(self) -> None:
        response = self.protocol.handle(
            request(9, 1, [Field("CFID", STRING, "OSDK_CLIENT")]),
            self.state,
        )[0]
        decoded = decode_frame(response)
        config = by_label(decoded, "CONF")
        self.assertEqual(config.type, MAP)
        self.assertEqual(
            dict(config.value[2]),
            {"ONLINE/NO_ASSET_UPDATE": "1"},
        )

    def test_xbox_login_returns_session_and_user_notification(self) -> None:
        login = request(
            1,
            170,
            [
                Field("GTAG", STRING, "TestGamer"),
                Field("MAIL", STRING, "test@example.invalid"),
                Field("XUID", INTEGER, 0x12345678),
            ],
        )
        response, notification = self.protocol.handle(login, self.state)
        decoded = decode_frame(response)
        self.assertEqual(by_label(decoded, "DSNM").value, "TestGamer")
        self.assertEqual(by_label(decoded, "XREF").value, 0x12345678)
        self.assertEqual(by_label(decoded, "XTYP").value, 1)
        self.assertEqual(by_label(decoded, "STAS").value, 2)
        self.assertTrue(self.state.authenticated)

        added = decode_frame(notification)
        self.assertEqual(added["component"], 0x7802)
        self.assertEqual(added["command"], 2)
        self.assertEqual(added["message_type"], 2)
        self.assertEqual(by_label(added, "NAME").value, "TestGamer")

    def test_authentication2_login_uses_exact_fifa14_schema(self) -> None:
        external_id = 2535469248587161
        login = request(
            35,
            10,
            [
                Field("AUTH", STRING, "offline-fifa14-auth"),
                Field("EXTI", INTEGER, external_id),
            ],
        )
        response, authenticated_notification, notification, extended_notification = self.protocol.handle(
            login, self.state
        )
        decoded = decode_frame(response)
        self.assertEqual(
            [field.label for field in decoded["fields"]],
            ["ANON", "SESS", "SPAM", "UNDR"],
        )
        self.assertEqual(by_label(decoded, "BUID").value, external_id)
        self.assertEqual(by_label(decoded, "UID").value, external_id)
        persona = by_label(decoded, "PDTL")
        self.assertEqual(
            [field.label for field in persona.value],
            ["DSNM", "PID", "PLAT"],
        )
        self.assertEqual(by_label(decoded, "PLAT").value, 1)
        self.assertEqual(by_label(decoded, "UNDR").value, 0)
        self.assertTrue(self.state.authenticated)

        authenticated = decode_frame(authenticated_notification)
        self.assertEqual(
            (authenticated["component"], authenticated["command"]),
            (0x7802, 8),
        )
        self.assertEqual(
            [field.label for field in authenticated["fields"]],
            [
                "ALOC", "BUID", "DSNM", "FRST", "KEY", "LAST", "LLOG",
                "MAIL", "PID", "PLAT", "UID", "USTP", "XREF",
            ],
        )
        self.assertEqual(by_label(authenticated, "BUID").value, external_id)
        self.assertEqual(by_label(authenticated, "UID").value, external_id)
        self.assertEqual(by_label(authenticated, "XREF").value, external_id)
        self.assertEqual(by_label(authenticated, "DSNM").value, "OfflineFUT")
        self.assertEqual(by_label(authenticated, "ALOC").value, 1718765138)

        added = decode_frame(notification)
        self.assertEqual((added["component"], added["command"]), (0x7802, 2))
        self.assertEqual(
            [field.label for field in added["fields"]],
            ["DATA", "USER"],
        )
        self.assertEqual(by_label(added, "BPS").value, "ams")
        self.assertEqual(by_label(added, "EXID").value, external_id)

        extended = decode_frame(extended_notification)
        self.assertEqual((extended["component"], extended["command"]), (0x7802, 1))
        self.assertEqual(
            [field.label for field in extended["fields"]],
            ["DATA", "SUBS", "USID"],
        )
        self.assertEqual(by_label(extended, "SUBS").value, 1)
        self.assertEqual(by_label(extended, "USID").value, external_id)

    def test_postauth_emits_complete_fifa14_response(self) -> None:
        self.state.xuid = 0x12345678
        decoded = decode_frame(
            self.protocol.handle(request(9, 8), self.state)[0]
        )
        self.assertEqual(
            [field.label for field in decoded["fields"]],
            ["PSS", "TELE", "TICK", "UROP"],
        )

        pss = by_label(decoded, "PSS")
        self.assertEqual(
            [field.label for field in pss.value],
            ["ADRS", "CSIG", "OIDS", "PJID", "PORT", "RPRT", "TIID"],
        )
        self.assertEqual(SERVER.find_field(pss.value, "CSIG").type, BINARY)
        self.assertEqual(SERVER.find_field(pss.value, "OIDS").type, LIST)

        self.assertEqual(by_label(decoded, "PORT").value, 0)
        telemetry = SERVER.find_field(decoded["fields"], "TELE")
        ticker = SERVER.find_field(decoded["fields"], "TICK")
        options = SERVER.find_field(decoded["fields"], "UROP")
        self.assertIsNotNone(telemetry)
        self.assertIsNotNone(ticker)
        self.assertIsNotNone(options)
        self.assertEqual(SERVER.find_field(telemetry.value, "PORT").value, 6767)
        self.assertEqual(SERVER.find_field(ticker.value, "PORT").value, 6776)
        self.assertEqual(SERVER.find_field(options.value, "UID").value, 0x12345678)

    def test_cardhouse_new_user_flow(self) -> None:
        login = decode_frame(
            self.protocol.handle(request(2148, 101), self.state)[0]
        )
        self.assertEqual(login["error"], 0)
        self.assertIsNone(SERVER.find_field(login["fields"], "NAME"))

        missing = decode_frame(
            self.protocol.handle(request(2148, 104), self.state)[0]
        )
        self.assertEqual(missing["message_type"], 3)
        self.assertEqual(missing["error"], 1)
        self.assertEqual(missing["component"], 2148)

    def test_sponsored_events_url_is_non_empty_and_local(self) -> None:
        decoded = decode_frame(
            self.protocol.handle(request(0x081C, 3), self.state)[0]
        )
        self.assertEqual(decoded["error"], 0)
        self.assertEqual(
            [field.label for field in decoded["fields"]],
            ["URL"],
        )
        self.assertEqual(
            by_label(decoded, "URL").value,
            "http://192.0.2.35:18080/sponsored-events",
        )

    def test_telemetry_server_has_complete_blaze3_schema(self) -> None:
        decoded = decode_frame(
            self.protocol.handle(request(9, 5), self.state)[0]
        )
        self.assertEqual(
            [field.label for field in decoded["fields"]],
            [
                "ADRS", "ANON", "DISA", "FILT", "LOC", "NOOK", "PORT",
                "SDLY", "SESS", "SKEY", "SPCT", "STIM",
            ],
        )
        self.assertEqual(by_label(decoded, "ADRS").value, "192.0.2.35")
        self.assertEqual(by_label(decoded, "PORT").value, 6767)

    def test_early_osdk_and_blaze_components_return_typed_payloads(self) -> None:
        messages = decode_frame(
            self.protocol.handle(request(15, 2), self.state)[0]
        )
        self.assertEqual(by_label(messages, "MCNT").value, 0)

        association = decode_frame(
            self.protocol.handle(request(25, 6), self.state)[0]
        )
        self.assertEqual(by_label(association, "LMAP").value, (STRUCT, []))

        clubs = decode_frame(
            self.protocol.handle(request(11, 2600), self.state)[0]
        )
        self.assertEqual(
            [field.label for field in clubs["fields"]],
            ["CLDS", "MXEV", "MXRV", "PUHR", "SOVR", "STRT"],
        )

        key_scopes = decode_frame(
            self.protocol.handle(request(7, 15), self.state)[0]
        )
        self.assertEqual(by_label(key_scopes, "KSIT").value, (STRING, STRUCT, []))

        stat_groups = decode_frame(
            self.protocol.handle(request(7, 3), self.state)[0]
        )
        self.assertEqual(by_label(stat_groups, "GRPS").value, (STRUCT, []))

        periods = decode_frame(
            self.protocol.handle(request(7, 20), self.state)[0]
        )
        self.assertEqual(len(periods["fields"]), 14)
        self.assertTrue(all(field.value == 0 for field in periods["fields"]))

        settings = decode_frame(
            self.protocol.handle(request(2249, 1), self.state)[0]
        )
        item_type, setting_items = by_label(settings, "LSST").value
        self.assertEqual(item_type, STRUCT)
        self.assertEqual(SERVER.find_field(setting_items[0], "ID").value, "O_TKfilter")

        groups = decode_frame(
            self.protocol.handle(request(2249, 2), self.state)[0]
        )
        _, group_items = by_label(groups, "LGRP").value
        self.assertEqual(SERVER.find_field(group_items[0], "ID").value, "O_SG_TCKR")
        self.assertEqual(
            SERVER.find_field(group_items[0], "LSET").value,
            (STRING, ["O_TKfilter"]),
        )

        gates = decode_frame(
            self.protocol.handle(request(2268, 3), self.state)[0]
        )
        self.assertEqual(by_label(gates, "LIST").value, (STRUCT, []))

    def test_first_time_setting_is_loaded_and_saved(self) -> None:
        loaded = decode_frame(
            self.protocol.handle(
                request(
                    9,
                    10,
                    [
                        Field("KEY", STRING, "FirstTimeFlag"),
                        Field("UID", INTEGER, 0),
                    ],
                ),
                self.state,
            )[0]
        )
        self.assertEqual(by_label(loaded, "DATA").value, "0")

        saved = decode_frame(
            self.protocol.handle(
                request(
                    9,
                    11,
                    [
                        Field("DATA", STRING, "1"),
                        Field("KEY", STRING, "FirstTimeFlag"),
                        Field("UID", INTEGER, 0),
                    ],
                ),
                self.state,
            )[0]
        )
        self.assertEqual(saved["error"], 0)

        reloaded = decode_frame(
            self.protocol.handle(
                request(9, 10, [Field("KEY", STRING, "FirstTimeFlag")]),
                self.state,
            )[0]
        )
        self.assertEqual(by_label(reloaded, "DATA").value, "1")


class TcpServerTests(unittest.TestCase):
    def test_fut_boot_xml_has_required_native_parser_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal_path = Path(temp) / "journal.jsonl"
            journal = SERVER.Journal(journal_path)
            identity = SERVER.IdentityHttpService("127.0.0.1", 0, "127.0.0.1", journal)
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("GET", "/futBoot.xml")
                response = client.getresponse()
                body = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    response.getheader("Content-Type"),
                    "application/xml; charset=utf-8",
                )
                self.assertEqual(body, SERVER.FUT_BOOT_XML)
                for required in (
                    b"<FutCfg>",
                    b"<cfgVersion>1</cfgVersion>",
                    b"<minorVersion>1</minorVersion>",
                    b"<bootString>fe/fut/servercalls</bootString>",
                    b"<futSubVersion>1</futSubVersion>",
                    b"<Language>",
                    b"<dimeUniqueId>1</dimeUniqueId>",
                    b"<key>",
                    b"<futKeyType>1</futKeyType>",
                ):
                    self.assertIn(required, body)
                client.close()

                journal_text = journal_path.read_text(encoding="utf-8")
                self.assertIn('"event": "fut_boot_served"', journal_text)
            finally:
                identity.stop()

    def test_identity_http_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            identity = SERVER.IdentityHttpService("127.0.0.1", 0, "127.0.0.1", journal)
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("GET", "/connect/auth?response_type=code")
                response = client.getresponse()
                self.assertEqual(response.status, 302)
                self.assertEqual(
                    response.getheader("Location"),
                    f"http://127.0.0.1:{port}/connect/redirect?code=offline-fifa14-auth",
                )
                response.read()
                client.close()
            finally:
                identity.stop()

    def test_fragmented_ping_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            protocol = SERVER.Fifa14Protocol("127.0.0.1", 10041, journal)
            service = SERVER.BlazeService("127.0.0.1", [0], protocol, journal)
            service.start()
            try:
                port = service.listeners[0].getsockname()[1]
                wire = request(9, 2)
                with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                    client.sendall(wire[:5])
                    time.sleep(0.01)
                    client.sendall(wire[5:])
                    header = client.recv(12)
                    self.assertEqual(len(header), 12)
                    payload_size = int.from_bytes(header[:2], "big")
                    payload = b""
                    while len(payload) < payload_size:
                        payload += client.recv(payload_size - len(payload))
                decoded = decode_frame(header + payload)
                self.assertEqual((decoded["component"], decoded["command"]), (9, 2))
                self.assertEqual(decoded["message_number"], 0x12345)
                self.assertIsNotNone(SERVER.find_field(decoded["fields"], "STIM"))
            finally:
                service.stop()


if __name__ == "__main__":
    unittest.main()
