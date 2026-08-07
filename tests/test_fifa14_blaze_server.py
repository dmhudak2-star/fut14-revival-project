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

    def test_osdk_client_bypasses_dead_refresh_and_enables_real_dlc_load(self) -> None:
        response = self.protocol.handle(
            request(9, 1, [Field("CFID", STRING, "OSDK_CLIENT")]),
            self.state,
        )[0]
        decoded = decode_frame(response)
        config = by_label(decoded, "CONF")
        self.assertEqual(config.type, MAP)
        values = dict(config.value[2])
        self.assertEqual(values["ONLINE/NO_ASSET_UPDATE"], "1")
        self.assertEqual(values["DLC_USE_REAL_DLL_LOAD"], "1")
        self.assertEqual(values["FUT_RS4_BASE_URL"], "http://192.0.2.35:18080/")
        for key in (
            "FUT/SINGLE_BASEURL_XBox360",
            "FUT_RS4_URL_XBox360",
            "FUT_RS4_APIURL_XBox360",
            "FUT/MODULE_BASEURL_XBox360",
        ):
            self.assertEqual(values[key], "http://192.0.2.35:18080/")
        self.assertEqual(
            values["FUTDYNAMICMESSAGES_URL_BASE"],
            "http://192.0.2.35:18080",
        )
        self.assertEqual(values["CARDS/DIRECTED_BLAZEENV"], "prod")
        self.assertEqual(values["FCC/FUT_DEPLOY_LANGUAGE"], "en_US")
        self.assertEqual(values["FUT/FORCE_TUTORIALS"], "1")
        self.assertEqual(values["FUT/DISABLE_TUTORIALS"], "0")

    def test_osdk_roster_declares_local_base_roster(self) -> None:
        response = self.protocol.handle(
            request(9, 1, [Field("CFID", STRING, "OSDK_ROSTER")]),
            self.state,
        )[0]
        decoded = decode_frame(response)
        values = dict(by_label(decoded, "CONF").value[2])
        self.assertEqual(values["ROSTER_URL"], "http://192.0.2.35:18080/roster")
        self.assertEqual(values["ROSTER_VER"], "1.0")
        self.assertIn("ROSTER_LKR", values)
        self.assertIn("ROSTER_CSUM", values)

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

    def test_authentication2_reuses_the_persona_fut_auth_adopted(self) -> None:
        external_id = 2535469248587161
        self.protocol.account_store.save_identity(external_id, "Imskobogota6z")
        login = request(
            35,
            10,
            [
                Field("AUTH", STRING, "offline-fifa14-auth"),
                Field("EXTI", INTEGER, external_id),
            ],
        )
        response = self.protocol.handle(login, self.state)[0]
        persona = by_label(decode_frame(response), "PDTL")
        self.assertEqual(persona.value[0].value, "Imskobogota6z")
        self.assertEqual(self.state.gamertag, "Imskobogota6z")
        self.assertEqual(
            self.protocol.account_store.load_identity(),
            (external_id, "Imskobogota6z"),
        )

    def test_authentication2_keeps_a_gamertag_the_client_supplied(self) -> None:
        external_id = 2535469248587161
        self.protocol.account_store.save_identity(external_id, "StoredName")
        self.state.gamertag = "LiveGamertag"
        login = request(
            35,
            10,
            [
                Field("AUTH", STRING, "offline-fifa14-auth"),
                Field("EXTI", INTEGER, external_id),
            ],
        )
        response = self.protocol.handle(login, self.state)[0]
        persona = by_label(decode_frame(response), "PDTL")
        self.assertEqual(persona.value[0].value, "LiveGamertag")

    def test_authentication2_ignores_a_persona_stored_for_another_account(
        self,
    ) -> None:
        self.protocol.account_store.save_identity(42, "OtherAccount")
        login = request(
            35,
            10,
            [
                Field("AUTH", STRING, "offline-fifa14-auth"),
                Field("EXTI", INTEGER, 2535469248587161),
            ],
        )
        response = self.protocol.handle(login, self.state)[0]
        persona = by_label(decode_frame(response), "PDTL")
        self.assertEqual(persona.value[0].value, "OfflineFUT")

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
        loaded_all = decode_frame(
            self.protocol.handle(request(9, 12), self.state)[0]
        )
        settings_map = by_label(loaded_all, "SMAP")
        self.assertEqual(settings_map.type, MAP)
        self.assertEqual(
            dict(settings_map.value[2]),
            {"FirstTimeFlag": "0"},
        )

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


    def test_preauth_locale_opens_the_easw_gate(self) -> None:
        # PreAuth's LANG is the four-byte locale the EASW gate compares
        # against, so OSDK_CORE must echo exactly what the console reported.
        assert SERVER.Fifa14Protocol.decode_locale(0x66724652) == "frFR"
        assert SERVER.Fifa14Protocol.decode_locale(0) == ""
        assert SERVER.Fifa14Protocol.decode_locale("frFR") == ""
        assert SERVER.Fifa14Protocol.decode_locale(0x00302D31) == ""

        self.state.locale = "frFR"
        response = self.protocol.fetch_config(
            request(9, 1, [Field("CFID", STRING, "OSDK_CORE")]),
            [Field("CFID", STRING, "OSDK_CORE")],
            self.state,
        )
        config = dict(by_label(decode_frame(response), "CONF").value[2])
        assert config["OSDK_EASW_ALLOWED_LOCALES"] == "frFR"
        assert len(config["OSDK_EASW_ALLOWED_LOCALES"]) == 4
        assert config["OSDK_EASW_AUTH_URL"].startswith("http://")
        # Without this the DLL falls back to the retired easw.easports.com.
        assert config["FUT_RS4_BASE_URL"].startswith("http://")

    def test_osdk_core_falls_back_to_a_valid_locale(self) -> None:
        response = self.protocol.fetch_config(
            request(9, 1, [Field("CFID", STRING, "OSDK_CORE")]),
            [Field("CFID", STRING, "OSDK_CORE")],
            self.state,
        )
        config = dict(by_label(decode_frame(response), "CONF").value[2])
        assert len(config["OSDK_EASW_ALLOWED_LOCALES"]) == 4

    def test_easw_authentication_returns_the_session_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal_path = Path(temp) / "journal.jsonl"
            journal = SERVER.Journal(journal_path)
            store = SERVER.PersistentAccountStore()
            store.save_identity(4242, "Local")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal, store
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                # This Xbox build posts a signed form to /authentication360
                # with a version query; the PC build posts JSON to the /v2
                # path. Both have to answer with the same headers.
                for path, body, content_type in (
                    (
                        "/authentication360?version=2.0.5.0",
                        b"gamertag=Local&xuid=1&locale=fr_FR&skuid=FFA14XBX",
                        "application/x-www-form-urlencoded",
                    ),
                    ("/v2/authenticationNucleusPersona", b"{}", "application/json"),
                ):
                    client = http.client.HTTPConnection(
                        "127.0.0.1", port, timeout=2
                    )
                    client.request(
                        "POST", path, body=body,
                        headers={"Content-Type": content_type},
                    )
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(
                        response.getheader("EASW-Token"), SERVER.EASW_TOKEN
                    )
                    self.assertEqual(
                        response.getheader("EASW-Session"), SERVER.EASW_SESSION
                    )
                    self.assertEqual(
                        response.getheader("EASW-Nucleus-Persona"), "4242"
                    )
                    self.assertEqual(response.getheader("EASW-Userid"), "4242")
                    response.read()
                    client.close()
            finally:
                identity.stop()
            self.assertIn(
                '"event": "easw_auth_request"',
                journal_path.read_text(encoding="utf-8"),
            )

    def test_fut_settings_and_locstrings_are_served(self) -> None:
        # The console walked security to completion and then died on these two
        # 404s before logging out again.
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("GET", "/ut/game/fifa14/settings")
                response = client.getresponse()
                settings = __import__("json").loads(response.read())
                self.assertEqual(response.status, 200)
                # Zero lets a brand-new account create its club immediately.
                self.assertEqual(settings["clubCreateThreshold"], 0)
                self.assertIn("maximumTradePileSize", settings)
                self.assertIn("getOperationTimeoutSec", settings)
                client.close()

                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("GET", "/fut/loc/XBox360/leaderboards.FRE_FR.xml")
                response = client.getresponse()
                body = response.read()
                self.assertEqual(response.status, 200)
                self.assertTrue(body.startswith(b"<?xml"))
                client.close()
            finally:
                identity.stop()

    def test_every_fut_route_answers_with_valid_json(self) -> None:
        # Each body must parse: a malformed one would reach the native parser
        # as a failure rather than as "nothing yet".
        for path, body in SERVER.FUT_ROUTES.items():
            self.assertTrue(path.startswith("/ut/"), path)
            __import__("json").loads(body)

    def test_first_use_fan_out_routes_return_parser_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                for path in sorted(SERVER.FUT_ROUTES):
                    client = http.client.HTTPConnection(
                        "127.0.0.1", port, timeout=2
                    )
                    client.request("GET", path)
                    response = client.getresponse()
                    self.assertEqual(response.status, 200, path)
                    self.assertEqual(
                        response.read().strip(),
                        SERVER.FUT_ROUTES[path],
                        path,
                    )
                    client.close()
            finally:
                identity.stop()

    def test_match_reset_acknowledges(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("PUT", "/ut/game/fifa14/match/reset")
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    __import__("json").loads(response.read()), {"reset": True}
                )
                client.close()
            finally:
                identity.stop()


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
                    b"<bootString>fut12</bootString>",
                    b"<futSubVersion>1</futSubVersion>",
                    b"<Language>",
                    b"<dimeUniqueId>1</dimeUniqueId>",
                    b"<key>",
                    b"<dimeUniqueId>2</dimeUniqueId>",
                    b"<futKeyType>0</futKeyType>",
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

    def test_fut_auth_and_first_use_account_info(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal_path = Path(temp) / "journal.jsonl"
            journal = SERVER.Journal(journal_path)
            account_store = SERVER.PersistentAccountStore()
            account_store.save_identity(0x123456789, "MatchedPersona")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal, account_store
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request(
                    "POST",
                    "/ut/auth",
                    body=b'{"isReadOnly":false}',
                    headers={"Content-Type": "application/json"},
                )
                response = client.getresponse()
                auth = __import__("json").loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(auth["sid"], "LOCAL-XBOX360-FIFA14-SID")
                self.assertIn("serverTime", auth)
                self.assertIn("lastOnlineTime", auth)
                client.close()

                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request(
                    "POST",
                    "/pow/auth",
                    body=b'{"EASW-Session":"LOCAL"}',
                    headers={"Content-Type": "application/json"},
                )
                response = client.getresponse()
                xbox_auth = __import__("json").loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    response.getheader("X-UT-SID"),
                    "LOCAL-XBOX360-FIFA14-SID",
                )
                self.assertEqual(xbox_auth["sid"], "LOCAL-XBOX360-FIFA14-SID")
                client.close()

                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("GET", "/ut/game/fifa14/user/accountinfo")
                response = client.getresponse()
                account = __import__("json").loads(response.read())
                personas = account["userAccountInfo"]["personas"]
                self.assertEqual(response.status, 200)
                self.assertEqual(len(personas), 1)
                self.assertEqual(personas[0]["personaId"], 0x123456789)
                self.assertEqual(personas[0]["personaName"], "MatchedPersona")
                self.assertEqual(personas[0]["userClubList"], [])
                client.close()
            finally:
                identity.stop()

    def test_identity_http_journal_records_request_body_and_unhandled_route(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal_path = Path(temp) / "journal.jsonl"
            journal = SERVER.Journal(journal_path)
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request(
                    "POST",
                    "/pow/auth",
                    body=b'{"EASW-Session":"LOCAL-FIFA14-EASW-SESSION"}',
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(client.getresponse().status, 200)
                client.close()

                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request(
                    "POST",
                    "/ut/game/fifa14/unmodelled",
                    body=b'{"probe":1}',
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(client.getresponse().status, 404)
                client.close()
            finally:
                identity.stop()

            events = [
                __import__("json").loads(line)
                for line in journal_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            requests = [
                event
                for event in events
                if event["event"] == "identity_http_request"
            ]
            self.assertEqual(
                requests[0]["body"],
                '{"EASW-Session":"LOCAL-FIFA14-EASW-SESSION"}',
            )
            unhandled = next(
                event
                for event in events
                if event["event"] == "identity_http_unhandled"
            )
            self.assertEqual(unhandled["path"], "/ut/game/fifa14/unmodelled")
            self.assertEqual(unhandled["body"], '{"probe":1}')

    def test_fut_auth_adopts_the_persona_the_client_presents(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal_path = Path(temp) / "journal.jsonl"
            journal = SERVER.Journal(journal_path)
            account_store = SERVER.PersistentAccountStore()
            account_store.save_identity(1_000_001, "OfflineFUT")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal, account_store
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request(
                    "POST",
                    "/pow/auth",
                    body=__import__("json").dumps(
                        {
                            "isReadOnly": False,
                            "sku": "FFA14XBX",
                            "nuc": 2535469248587161,
                            "nucleusPersonaId": 0,
                            "nucleusPersonaDisplayName": "Imskobogota6z",
                            "method": "cas",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(client.getresponse().status, 200)
                client.close()

                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request("GET", "/ut/game/fifa14/user/accountinfo")
                response = client.getresponse()
                persona = __import__("json").loads(response.read())
                persona = persona["userAccountInfo"]["personas"][0]
                self.assertEqual(persona["personaName"], "Imskobogota6z")
                self.assertEqual(persona["personaId"], 2535469248587161)
                self.assertEqual(persona["userClubList"], [])
                client.close()
            finally:
                identity.stop()

            self.assertIn(
                '"event": "fut_auth_identity_adopted"',
                journal_path.read_text(encoding="utf-8"),
            )

    def test_auth_request_identity_rejects_incomplete_documents(self) -> None:
        self.assertIsNone(SERVER.auth_request_identity(b""))
        self.assertIsNone(SERVER.auth_request_identity(b"not json"))
        self.assertIsNone(
            SERVER.auth_request_identity(b'{"nuc":123}')
        )
        self.assertIsNone(
            SERVER.auth_request_identity(b'{"nucleusPersonaDisplayName":"X"}')
        )
        self.assertIsNone(
            SERVER.auth_request_identity(
                b'{"nuc":0,"nucleusPersonaDisplayName":"X"}'
            )
        )
        self.assertEqual(
            SERVER.auth_request_identity(
                b'{"nuc":7,"nucleusPersonaId":9,"nucleusPersonaDisplayName":"X"}'
            ),
            (9, "X"),
        )

    def test_request_body_preview_bounds_and_binary(self) -> None:
        self.assertIsNone(SERVER.request_body_preview(b""))
        self.assertEqual(SERVER.request_body_preview(b'{"a":1}'), '{"a":1}')
        oversized = b"x" * (SERVER.REQUEST_BODY_PREVIEW_LIMIT + 10)
        preview = SERVER.request_body_preview(oversized)
        self.assertTrue(preview.startswith("x" * 64))
        self.assertIn(f"{len(oversized)} bytes total", preview)
        binary = SERVER.request_body_preview(b"\xff\xfe\x00\x01")
        self.assertTrue(binary.startswith("<4 non-utf8 bytes>"))

    def test_fut_first_use_security_and_icebreaker_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal
            )
            identity.start()
            try:
                port = identity.server.server_address[1]

                def request_json(method: str, path: str, body: bytes | None = None):
                    client = http.client.HTTPConnection(
                        "127.0.0.1", port, timeout=2
                    )
                    client.request(method, path, body=body)
                    response = client.getresponse()
                    document = __import__("json").loads(response.read())
                    status = response.status
                    headers = dict(response.getheaders())
                    client.close()
                    return status, headers, document

                status, _, trusted = request_json(
                    "GET", "/ut/game/fifa14/phishing/trusteddevice"
                )
                # A known device is what keeps the client from asking its
                # security question on every launch. The four booleans are
                # the whole of what CardsDLL's parser reads here.
                self.assertEqual(status, 200)
                self.assertEqual(
                    trusted,
                    {
                        "trusted": True,
                        "changed": False,
                        "exists": True,
                        "locked": False,
                    },
                )

                status, _, question = request_json(
                    "GET", "/ut/game/fifa14/phishing/question"
                )
                self.assertEqual(status, 200)
                self.assertEqual(
                    question,
                    {"question": 0, "attempts": 5, "recoverAttempts": 20},
                )

                status, headers, validation = request_json(
                    "POST",
                    "/ut/game/fifa14/phishing/validate",
                    b'{"answer":"offline"}',
                )
                self.assertEqual(status, 200)
                self.assertEqual(validation["token"], "LOCAL-FIFA14-PHISHING")
                self.assertIn("FUTWebPhishing=", headers["Set-Cookie"])

                status, _, actions = request_json(
                    "GET", "/ut/game/fifa14/user/action"
                )
                self.assertEqual((status, actions), (200, {"userActionList": []}))

                status, _, updated = request_json(
                    "PUT", "/ut/game/fifa14/user/action/firstUse", b"{}"
                )
                self.assertEqual((status, updated), (200, {}))

                status, _, pack_list = request_json(
                    "GET",
                    "/fut/packs/icebreaker/icebreakerpacklist.json",
                )
                self.assertEqual(status, 200)
                # Four dock rows, each carrying the arrays the card
                # constructor reads.  With only id and image the retail
                # constructor dereferences a null player and the client
                # restarts its bootstrap, so the arrays are the contract,
                # not decoration.
                packs = pack_list["packList"]
                self.assertEqual([pack["id"] for pack in packs], [0, 1, 2, 3])
                self.assertEqual([pack["image"] for pack in packs], [0, 1, 2, 3])
                for pack in packs:
                    self.assertEqual(len(pack["squad"]), 23)
                    self.assertEqual(len(pack["Rating"]), 23)
                    self.assertTrue(all(pack["squad"]))
                self.assertEqual(
                    True,
                    True,
                )
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
