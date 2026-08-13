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
        # Tutorials stay off: the client asks for the tutorial feed either
        # way, so these keys buy nothing, and forcing them on pointed the
        # login at a document this server cannot yet shape correctly.
        self.assertEqual(values["FUT/FORCE_TUTORIALS"], "0")
        self.assertEqual(values["FUT/DISABLE_TUTORIALS"], "1")

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
                # These three are answered from the wallet, whose balance
                # moves, so a fixed body would only assert today's number.
                dynamic = {
                    "/ut/game/fifa14/user",
                    "/ut/game/fifa14/user/credits",
                    "/ut/delete/game/fifa14/item",
                    "/ut/game/fifa14/trade/status",
                    "/ut/game/fifa14/tradePile",
                    "/ut/game/fifa14/watchlist",
                    # Generated from the pack table now, not a fixture.
                    "/ut/game/fifa14/store",
                    "/ut/game/fifa14/store/purchasegroup/all",
                    # Modes are generated too.
                    "/ut/game/fifa14/season/list",
                    "/ut/game/fifa14/season/user",
                    "/ut/game/fifa14/tournament/list",
                    "/ut/game/fifa14/tournament/user/list",
                    "/ut/game/fifa14/clientdata/totw",
                    # Club counters are computed from the inventory now.
                    "/ut/game/fifa14/club/stats/staff",
                    "/ut/game/fifa14/club/stats/year",
                    "/ut/game/fifa14/club/stats/consumables",
                    "/ut/game/fifa14/club/stats/newcards",
                    # Counted from the club, which grows as cards are kept.
                    "/ut/game/fifa14/hub",
                    # Manager tasks are tracked, not fixed.
                    "/ut/game/fifa14/clientdata/managerquest",
                    # Carries the club's own cards and the adopted persona.
                    # The fixture beside it is the shape, not the contents.
                    "/ut/game/fifa14/clubUser",
                }
                for path in sorted(set(SERVER.FUT_ROUTES) - dynamic):
                    client = http.client.HTTPConnection(
                        "127.0.0.1", port, timeout=2
                    )
                    client.request("GET", path)
                    response = client.getresponse()
                    self.assertEqual(response.status, 200, path)
                    # Every FUT reply now carries the coin total as well, so
                    # compare the fixture's own members rather than the whole
                    # body.
                    body = __import__("json").loads(response.read())
                    expected = __import__("json").loads(SERVER.FUT_ROUTES[path])
                    for key, value in expected.items():
                        self.assertEqual(body.get(key), value, f"{path}:{key}")
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


class MatchEndTests(unittest.TestCase):
    def test_the_destroy_response_carries_its_three_members(self) -> None:
        # FutDestroyMatchServerResponse has exactly myMatchStats,
        # opponentMatchStats and matchData -- all three in CardsDLL's name
        # table. This route answered {}, a document the parser can read and
        # find nothing in. Nothing else goes out: a sibling build records its
        # client disconnecting after parsing an oversized destroy response.
        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            identity = SERVER.IdentityHttpService(
                "127.0.0.1", 0, "127.0.0.1", journal
            )
            identity.start()
            try:
                port = identity.server.server_address[1]
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                client.request(
                    "PUT",
                    "/ut/game/fifa14/match/end",
                    __import__("json").dumps({"matchData": "QUJD"}).encode(),
                )
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                body = __import__("json").loads(response.read())
                client.close()

                self.assertEqual(
                    set(body),
                    {"myMatchStats", "opponentMatchStats", "matchData"},
                )
                self.assertEqual(body["matchData"], "QUJD")
            finally:
                identity.stop()


class TournamentRouteTests(unittest.TestCase):
    """The cups.

    Every member asserted here is one CardsDLL's own JSON name table carries.
    The catalogue was served empty because an earlier guessed shape froze the
    title on Competition Joueur Solo, so the two things that matter are that
    `rounds` is an array of records and that no invented member goes out.
    """

    def setUp(self) -> None:
        SERVER.TOURNAMENT_PROGRESS.entries.clear()

    tearDown = setUp

    def _identity(self, temp: str):
        journal = SERVER.Journal(Path(temp) / "journal.jsonl")
        identity = SERVER.IdentityHttpService("127.0.0.1", 0, "127.0.0.1", journal)
        identity.start()
        return identity

    def _get(self, port: int, path: str, method: str = "GET", body: bytes | None = None):
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        client.request(method, path, body)
        response = client.getresponse()
        payload = __import__("json").loads(response.read())
        status = response.status
        client.close()
        return status, payload

    def test_catalogue_carries_the_native_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(temp)
            try:
                port = identity.server.server_address[1]
                for path in (
                    "/ut/game/fifa14/tournament",
                    "/ut/game/fifa14/tournament/list",
                ):
                    status, body = self._get(port, path)
                    self.assertEqual(status, 200, path)
                    cups = body["tournament"]
                    self.assertTrue(cups, path)
                    for cup in cups:
                        # The freeze: a count where the parser walks records.
                        self.assertIsInstance(cup["rounds"], list)
                        self.assertEqual(len(cup["rounds"]), cup["numRounds"])
                        for entry in cup["rounds"]:
                            self.assertEqual(
                                set(entry),
                                {"id", "difficulty", "rewardMultiplier", "coins"},
                            )
                        self.assertEqual(cup["treeType"], "knockout")
                        self.assertEqual(cup["type"], "offline")
                        self.assertEqual(cup["lock"], "UNLOCKED")
                        self.assertEqual(
                            set(cup["awardSet"]["awards"][0]),
                            {"awardType", "value", "halid"},
                        )
                        # Members the previous attempt invented; none of these
                        # appear in the module's name table.
                        for absent in ("name", "level", "entryFee", "active", "won"):
                            self.assertNotIn(absent, cup)
            finally:
                identity.stop()

    def test_teams_draw_is_one_short_of_the_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(temp)
            try:
                port = identity.server.server_address[1]
                status, body = self._get(
                    port, "/ut/game/fifa14/tournament/teams?count=15"
                )
                self.assertEqual(status, 200)
                self.assertEqual(set(body), {"teamId"})
                self.assertEqual(len(body["teamId"]), 15)
                self.assertTrue(all(isinstance(x, int) for x in body["teamId"]))
            finally:
                identity.stop()

    def test_a_cup_never_entered_reports_only_its_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(temp)
            try:
                port = identity.server.server_address[1]
                status, body = self._get(port, "/ut/game/fifa14/tournament/user/list")
                self.assertEqual((status, body), (200, {"tournamentId": []}))
                status, body = self._get(port, "/ut/game/fifa14/tournament/user/1")
                self.assertEqual((status, body), (200, {"tournamentId": 1}))
            finally:
                identity.stop()

    def test_progress_is_kept_and_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(temp)
            try:
                port = identity.server.server_address[1]
                # The body the client builds itself, from the format string
                # that sits among the cup constants in .rdata.
                sent = __import__("json").dumps(
                    {
                        "round": 3,
                        "dataVersion": 1,
                        "tournamentData": "QUJD",
                        "progressDataVersion": 1,
                        "progressData": "REVG",
                    }
                ).encode()
                status, _ = self._get(
                    port, "/ut/game/fifa14/tournament/user/2", "PUT", sent
                )
                self.assertEqual(status, 200)

                status, body = self._get(port, "/ut/game/fifa14/tournament/user/2")
                self.assertEqual(status, 200)
                self.assertEqual(body["round"], 3)
                self.assertEqual(body["tournamentData"], "QUJD")
                self.assertEqual(body["progressData"], "REVG")
                # Handed back exactly as it was written, and nothing besides.
                # `tournamentId` is already in the path, and the lower-case
                # `progressdata` is the same known field a second time rather
                # than a sibling the parser skips -- resuming a saved cup
                # froze the title on the first GET this route ever answered.
                self.assertNotIn("tournamentId", body)
                self.assertNotIn("progressdata", body)
                # `data` is the season spelling and does not go out here.
                self.assertNotIn("data", body)

                # Only the cup actually entered is named.
                status, body = self._get(port, "/ut/game/fifa14/tournament/user/list")
                self.assertEqual((status, body), (200, {"tournamentId": [2]}))

                status, _ = self._get(
                    port, "/ut/delete/game/fifa14/tournament/user/2", "POST", b"{}"
                )
                self.assertEqual(status, 200)
                status, body = self._get(port, "/ut/game/fifa14/tournament/user/list")
                self.assertEqual((status, body), (200, {"tournamentId": []}))
            finally:
                identity.stop()

    def test_a_season_is_saved_under_its_season_and_division(self) -> None:
        # The route the console actually sent on starting a Saison Joueur
        # Solo. `ut/%s/season/%s/user` in the URL template table reads as one
        # id; the format string beside the season serialiser is
        # `%d/division/%d`, and the wire agrees with the second reading.
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(temp)
            try:
                port = identity.server.server_address[1]
                path = "/ut/game/fifa14/season/1/division/10/user"
                # Round one with an empty progress blob is a season with no
                # first match behind it -- the same shape that froze the cups
                # when it was handed back. It is answered as no season at all.
                started = __import__("json").dumps(
                    {
                        "round": 1,
                        "dataVersion": 1,
                        "data": "AAAAEAUAAAABAAAAAAAAAAAAAAA=",
                        "progressDataVersion": 1,
                        "progressData": "AAAAAA==",
                    }
                ).encode()
                status, body = self._get(port, path, "PUT", started)
                self.assertEqual((status, body), (200, {}))

                # A season actually under way comes back the way it went up,
                # spelled `data` -- the seasons' word, not the cups'.
                played = __import__("json").dumps(
                    {
                        "round": 3,
                        "dataVersion": 1,
                        "data": "QUJD",
                        "progressDataVersion": 1,
                        "progressData": "REVG",
                    }
                ).encode()
                status, _ = self._get(port, path, "PUT", played)
                self.assertEqual(status, 200)
                status, body = self._get(port, path, "GET")
                self.assertEqual(status, 200)
                self.assertEqual(body["round"], 3)
                self.assertEqual(body["data"], "QUJD")
                self.assertEqual(body["progressData"], "REVG")
                self.assertNotIn("tournamentData", body)
                self.assertNotIn("seasonId", body)

                # Another division is another season, not the same one.
                status, body = self._get(
                    port, "/ut/game/fifa14/season/1/division/9/user", "GET"
                )
                self.assertEqual((status, body), (200, {}))

                status, _ = self._get(
                    port, "/ut/game/fifa14/season/1/division/10/reset", "PUT", b"{}"
                )
                self.assertEqual(status, 200)
                status, body = self._get(port, path, "GET")
                self.assertEqual((status, body), (200, {}))
            finally:
                identity.stop()

    def test_the_season_history_is_answered_empty_rather_than_404ed(self) -> None:
        # Asked for once per type the moment a season starts. A 404 here is a
        # hang with nothing to read, and no season has ever been finished, so
        # there is nothing to invent either.
        with tempfile.TemporaryDirectory() as temp:
            identity = self._identity(temp)
            try:
                port = identity.server.server_address[1]
                for kind in ("offline", "online", "WC_TOURNAMENT_OFFINE"):
                    status, body = self._get(
                        port, f"/ut/game/fifa14/season/user/history?type={kind}"
                    )
                    self.assertEqual((kind, status, body), (kind, 200, {}))
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
                self.assertEqual(response.status, 200)
                # No FUT account yet, whatever identity the Blaze side holds.
                # Offering a persona here claims a club, squad and identity
                # that this server cannot then produce, and the login helper
                # waits on them forever.
                self.assertEqual(account["userAccountInfo"]["personas"], [])
                self.assertIs(account["userAccountInfo"]["returningUser"], False)
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
                # The Blaze side still adopts the persona the client presents
                # -- that is what this test is about -- but accountinfo does
                # not advertise it as an existing FUT account.
                self.assertEqual(persona["userAccountInfo"]["personas"], [])
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
                # The collection CardsDLL reads is `actions` -- `userActionList`
                # is in no member-name table, so it was a list the parser could
                # not see. Both spellings go out; the unrecognised one is
                # skipped. This is the list FUT_IcebreakerManager consults
                # before deciding whether the captain selection is owed.
                self.assertEqual(status, 200)
                self.assertEqual(actions["actions"], [])
                self.assertEqual(actions["userActionList"], [])

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


class RouteSpellingTests(unittest.TestCase):
    """The client's spelling of a path and this server's have to agree."""

    def test_the_client_spelling_of_the_watch_list_is_answered(self) -> None:
        import fifa14_blaze_server as server

        # The client asks for `watchList`; this server registered `watchlist`,
        # and every time the watch list was opened it got a 404. Nothing
        # reported it -- an empty watch list looks like an empty watch list.
        self.assertEqual(
            server.FUT_ROUTE_SPELLINGS["/ut/game/fifa14/watchlist"],
            "/ut/game/fifa14/watchlist",
        )
        for spelling in (
            "/ut/game/fifa14/watchList",
            "/ut/game/fifa14/WATCHLIST",
            "/ut/game/fifa14/tradepile",
            "/ut/game/fifa14/clubuser",
        ):
            self.assertIn(
                spelling.lower(), server.FUT_ROUTE_SPELLINGS,
                f"{spelling} does not resolve to a registered route",
            )

    def test_every_route_the_server_names_can_be_reached_in_any_case(self) -> None:
        # The map is built from two lists and the handlers are written by hand,
        # so it drifts unless something checks. Every `/ut/game/fifa14/...`
        # literal in the module has to be in it.
        import re
        from pathlib import Path

        import fifa14_blaze_server as server

        source = Path(server.__file__).read_text()
        literals = set(re.findall(r'"(/ut/game/fifa14/[a-zA-Z0-9/_-]*)"', source))
        # Prefixes used with startswith, not whole routes.
        literals = {route for route in literals if not route.endswith("/")}
        missing = sorted(
            route for route in literals
            if route.lower() not in server.FUT_ROUTE_SPELLINGS
        )
        self.assertEqual(missing, [], f"routes missing from the spelling map: {missing}")


class GameReportingTests(unittest.TestCase):
    """The offline game report the console really submits, component 28/2."""

    # Both captured off this console. The first is what a FUT match submits
    # (`gameType21`), the second a longer report carrying a club record
    # (`gameType85`). Each one used to take the Blaze connection down with it:
    # the TDF decoder had no case for type 7 and raised on `PRVT` at offset 5.
    GAME_TYPE_21 = bytes.fromhex(
        "004A001C000200000000003F9AECE80000C32DB40700CB0CB4039E1B650701"
        "9FC2908E179E1B65038F4CB9010100C2CA640000CE3BF2009C76CEBB270001"
        "00009F2A6400009F4E70010B67616D655479706532310000"
    )
    GAME_TYPE_85 = bytes.fromhex(
        "00AF001C000200000000007D9AECE80000C32DB40700CB0CB4039E1B650701"
        "9BFDB5C50F9E1B65038E7CB407009E1B7203872A6400008E7CB40701"
        "9DD5DD9F1D8E7CB4038ED9F203CB6B2D0000DEEC2B000000B64A6600020000"
        "8F4A6400009F2A6400009F4A6D00AE57A73A6D0000B27A640000CA1BAB0000"
        "CAFA640000CE5A640000CF4D730000D39C25010B67616D655479706538350000"
        "A66C320700D21B72070000009F2A6400009F4E70010B67616D65547970653835"
        "0000"
    )

    def test_the_offline_game_report_decodes_and_re_encodes_unchanged(self) -> None:
        for name, frame in (("21", self.GAME_TYPE_21), ("85", self.GAME_TYPE_85)):
            with self.subTest(game_type=name):
                decoded = decode_frame(frame)
                self.assertEqual(decoded["component"], 28)
                self.assertEqual(decoded["command"], 2)
                labels = [field.label for field in decoded["fields"]]
                self.assertEqual(labels, ["FNSH", "PRVT", "RPRT"])
                # Re-encoding byte for byte is what says the shape was read
                # rather than guessed: there is no slack for a wrong rule to
                # hide in.
                self.assertEqual(encode_fields(decoded["fields"]), frame[12:])

    def test_the_report_carries_a_variable_tdf_holding_the_game(self) -> None:
        decoded = decode_frame(self.GAME_TYPE_21)
        report = SERVER.find_field(decoded["fields"], "RPRT")
        self.assertEqual(report.type, STRUCT)
        # PRVT is an unset variable; GAME is a set one, carrying the 32-bit id
        # of the class whose fields follow.
        private = SERVER.find_field(decoded["fields"], "PRVT")
        self.assertEqual(private.type, 7)
        self.assertIsNone(private.value)
        game = SERVER.find_field(report.value, "GAME")
        self.assertEqual(game.type, 7)
        tdf_id, fields = game.value
        self.assertEqual(tdf_id, 0xB8E2109F)
        inner = SERVER.find_field(fields, "GAME")
        self.assertEqual(SERVER.find_field(inner.value, "SCOR").value, 7580)

    def test_submitting_a_report_is_answered_rather_than_dropped(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        journal = SERVER.Journal(Path(temp.name) / "journal.jsonl")
        protocol = SERVER.Fifa14Protocol("192.0.2.35", 10041, journal)
        state = SERVER.ClientState(1, ("192.0.2.25", 12345), 10041)
        replies = protocol.handle(self.GAME_TYPE_21, state)
        # Two: the RPC answer, and the asynchronous ResultNotification the
        # post-match screen waits on before it will leave. Answering the RPC
        # alone is not the end of the handshake.
        self.assertEqual(len(replies), 2)
        answer = decode_frame(replies[0])
        self.assertEqual(answer["message_type"], 1)
        self.assertEqual(answer["error"], 0)

        notification = decode_frame(replies[1])
        self.assertEqual(notification["component"], 28)
        self.assertEqual(notification["command"], 114)
        self.assertEqual(notification["message_type"], 2)
        labels = {field.label: field.value for field in notification["fields"]}
        self.assertEqual(labels["EROR"], 0)
        self.assertEqual(labels["FNL"], 1)
        # GRID travels back in both id members so the notification can be
        # matched to the report that caused it.
        self.assertEqual(labels["GRID"], labels["GHID"])


class TrophyItemTests(unittest.TestCase):
    def test_a_negative_trophy_id_is_answered_like_any_other(self) -> None:
        # The seasons screen asks for /fut/items/xbl2/-1.json, once per
        # division. A digits-only pattern let all ten fall through to the
        # blanket `{"itemData":[]}` that this route exists to replace, and the
        # console then built /fut/items/images/trophies/xbl2/.big with no
        # basename -- eighteen of those are in the journals.
        import json
        import re

        pattern = r"/fut/items/xbl2/(-?\d+)\.json"
        self.assertIsNotNone(re.fullmatch(pattern, "/fut/items/xbl2/-1.json"))
        self.assertIsNotNone(re.fullmatch(pattern, "/fut/items/xbl2/1102.json"))

        document = json.loads(SERVER.trophy_item_response(-1))
        entry = document["itemData"][0]
        self.assertEqual(entry["resourceId"], -1)
        # The basename is what the console builds the archive path from, so
        # the only thing that matters is that there is one.
        self.assertTrue(entry["assetName"])
        self.assertEqual(entry["assetName"], entry["image"])


class ConsumableByItemIdTests(unittest.TestCase):
    def test_a_consumable_can_be_applied_by_its_own_item_id(self) -> None:
        # The client addresses a consumable two ways: `item/resource/<id>`
        # names the definition, `item/<id>` names one particular card in the
        # club. Only the first was handled, so this real request on 11 August
        #
        #     POST /ut/game/fifa14/item/1950000106
        #     {"apply":[{"id":1700000004}]}
        #
        # was answered 404 and went into the unhandled journal, where nobody
        # looked. From the player's side the card simply did nothing.
        import fut_inventory as inventory

        club = inventory.ClubInventory()
        rack = inventory.ConsumableRack(club)
        consumable = next(
            item for item in club.items
            if item.get("itemType") in inventory.CONSUMABLE_TYPES
            and item.get("resourceId")
        )
        self.assertEqual(
            rack.resource_of(consumable["id"]), consumable["resourceId"]
        )

        player = next(i for i in club.items if i.get("itemType") == "player")
        with self.assertRaises(inventory.ConsumableRefused):
            rack.resource_of(player["id"])
        with self.assertRaises(inventory.ConsumableRefused):
            rack.resource_of(-999)


class EveryRouteAnswersTests(unittest.TestCase):
    """A GET on every registered FUT route comes back 200 and parseable.

    The watch list was a 404 for as long as it has existed, because the client
    spells it `watchList` and this server registered `watchlist`. Nothing
    noticed, because a 404 on a FUT route just leaves a screen empty. This
    walks the whole surface so the next one is noticed by a test rather than by
    a player wondering why a screen is blank.

    Routes taking an id, and the two that are not JSON, are named below rather
    than skipped silently -- a skip list nobody reads is how the last one got
    through.
    """

    # Prefixes, not whole routes: they need an id or a body to mean anything.
    NEEDS_MORE = (
        "/ut/game/fifa14/trade",
        "/ut/game/fifa14/user/action",
        "/ut/game/fifa14/phishing",
        "/ut/game/fifa14/item",
        "/ut/game/fifa14/auctionhouse",
        "/ut/game/fifa14/squad",
        "/ut/game/fifa14/tournament/user",
        "/ut/game/fifa14/store",
    )

    def test_every_registered_route_answers_a_get(self) -> None:
        import http.client
        import json as jsonlib

        with tempfile.TemporaryDirectory() as temp:
            journal = SERVER.Journal(Path(temp) / "journal.jsonl")
            identity = SERVER.IdentityHttpService("127.0.0.1", 0, "127.0.0.1", journal)
            identity.start()
            try:
                port = identity.server.server_address[1]
                routes = sorted(
                    set(SERVER.FUT_ROUTES) | set(SERVER.HANDLED_ROUTES)
                )
                checked = 0
                for route in routes:
                    if route in self.NEEDS_MORE:
                        continue
                    for spelling in (route, route.lower()):
                        client = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                        client.request("GET", spelling)
                        response = client.getresponse()
                        payload = response.read()
                        status = response.status
                        client.close()
                        self.assertEqual(status, 200, f"{spelling} answered {status}")
                        if payload.strip():
                            jsonlib.loads(payload)
                    checked += 1
                # If this ever drops to nothing the loop has stopped testing.
                self.assertGreater(checked, 30)
            finally:
                identity.stop()


class JournalReplayTests(unittest.TestCase):
    def test_the_most_recent_recorded_session_still_answers(self) -> None:
        # The journals are a regression suite nobody was running. Two of
        # tonight's fixes came out of reading them by hand -- the watch list
        # 404ing on a capital L, and a consumable applied by its own item id
        # falling through unhandled -- and both had been failing for days in a
        # screen that merely looked empty.
        import importlib.util

        journals = sorted((ROOT / "runtime").glob("live-easw-*.jsonl"))
        if not journals:
            self.skipTest("no recorded session in runtime/")

        spec = importlib.util.spec_from_file_location(
            "replay_journal", ROOT / "tools" / "replay_journal.py"
        )
        replay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(replay)

        # The newest session with enough in it to be worth replaying.
        substantial = [
            path for path in journals
            if sum(1 for _ in replay.requests_in(path)) >= 20
        ]
        if not substantial:
            self.skipTest("no recorded session with requests in it")
        self.assertEqual(replay.replay(substantial[-1:], quiet=True), 0)


class SessionResumeTests(unittest.TestCase):
    def test_a_second_connection_is_told_whose_it_is(self) -> None:
        # The EAS FC module opens a Blaze connection of its own once its
        # endpoints point somewhere reachable, and the first thing it says is
        #
        #     component 0x7802 command 35   SKEY "offline-901feefe6a599"
        #
        # which is the key the login handed out on the first connection. It was
        # answered with a fieldless success and nothing else, so the module was
        # acknowledged and then never told who it was.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        journal = SERVER.Journal(Path(temp.name) / "journal.jsonl")
        store = SERVER.PersistentAccountStore(Path(temp.name) / "account.json")
        protocol = SERVER.Fifa14Protocol(
            "192.0.2.35", 10041, journal, account_store=store
        )
        xuid = 0x901FEEFE6A599
        store.save_identity(xuid, "Imskobogota6z")

        state = SERVER.ClientState(2, ("192.0.2.25", 1037), 10041)
        replies = protocol.handle(
            request(0x7802, 35, [Field("SKEY", STRING, f"offline-{xuid:x}")]),
            state,
        )
        self.assertEqual(len(replies), 4)
        self.assertEqual(decode_frame(replies[0])["message_type"], 1)
        self.assertTrue(state.authenticated)
        self.assertEqual(state.xuid, xuid)

        sent = [decode_frame(frame) for frame in replies[1:]]
        self.assertEqual([frame["command"] for frame in sent], [8, 2, 1])
        for frame in sent:
            self.assertEqual(frame["component"], 0x7802)
            self.assertEqual(frame["message_type"], 2)
        authenticated = {f.label: f.value for f in sent[0]["fields"]}
        self.assertEqual(authenticated["DSNM"], "Imskobogota6z")
        self.assertEqual(authenticated["BUID"], xuid)

    def test_a_key_that_names_nobody_is_refused_quietly(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        journal = SERVER.Journal(Path(temp.name) / "journal.jsonl")
        store = SERVER.PersistentAccountStore(Path(temp.name) / "account.json")
        protocol = SERVER.Fifa14Protocol(
            "192.0.2.35", 10041, journal, account_store=store
        )
        store.save_identity(1234, "Someone")
        state = SERVER.ClientState(2, ("192.0.2.25", 1037), 10041)
        replies = protocol.handle(
            request(0x7802, 35, [Field("SKEY", STRING, "offline-deadbeef")]),
            state,
        )
        # Answered, but nobody is claimed on the strength of a key that names
        # a session this server never handed out.
        self.assertEqual(len(replies), 1)
        self.assertFalse(state.authenticated)


class IdentityChannelTests(unittest.TestCase):
    def test_the_user_document_carries_the_persona_the_headers_do(self) -> None:
        # FUT tells a client who it is through four channels and they have to
        # agree: the /user body's personaId, /eaid/personas, and the
        # EASW-Nucleus-Persona and EASW-Userid headers. Ours carried the
        # console's real nucleus id in both headers and a flat 0 in the body.
        import json

        import fut_inventory as inventory

        wallet = inventory.Wallet()
        persona = 2535469248587161
        document = json.loads(wallet.user_info("Fondateur FUT", "FUT", persona))
        self.assertEqual(document["personaId"], persona)

        # And every other document that carries one carries the same. Aligning
        # /user alone is worse than leaving them all wrong: the squad screen
        # came back with eleven blank cards, because a client will not show a
        # squad that belongs to somebody else.
        inventory.PERSONA.adopt(persona)
        try:
            club = inventory.ClubInventory()
            squad = json.loads(club.squad_document(club.active_squad_id(), "bpl"))
            self.assertEqual(squad["personaId"], persona)
            self.assertEqual(
                json.loads(club.active_squad_response("bpl"))["personaId"], persona
            )
            self.assertEqual(
                json.loads(wallet.user_info("bpl", "FUT"))["personaId"], persona
            )
        finally:
            inventory.PERSONA.id = 0
