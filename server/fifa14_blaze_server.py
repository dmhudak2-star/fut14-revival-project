#!/usr/bin/env python3
"""Minimal, observable Blaze 3 server for FIFA 14 on Xbox 360.

This is deliberately a protocol bootstrap rather than a complete FUT server.
It answers the title's redirector, Util, Xbox authentication, UserSessions and
early CardHouse requests while recording every frame as JSON Lines.  Unknown
requests are returned as empty successful replies by default so the next
client request can be discovered without fabricating persistent FUT state.

The request/response layouts are derived from the public BlazeSDK and
ZamboniUltimateTeam projects.  No game data or captured credentials are used.
"""

from __future__ import annotations

import argparse
import http.server
import json
import signal
import socket
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REPOSITORY = Path(__file__).resolve().parents[1]
TOOLS = REPOSITORY / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from blaze_tdf import (  # noqa: E402
    BINARY,
    INTEGER,
    LIST,
    MAP,
    STRING,
    STRUCT,
    UNION,
    Decoder,
    Field,
    decode_frame,
    encode_fields,
    encode_frame,
    json_value,
)


REDIRECTOR = 5
UTIL = 9
AUTHENTICATION = 1
AUTHENTICATION2 = 35
USER_SESSIONS = 0x7802
CARDHOUSE = 2148
SPONSORED_EVENTS = 0x081C
STATS = 7
CENSUS_DATA = 10
CLUBS = 11
MESSAGING = 15
ROOMS = 21
ASSOCIATION_LISTS = 25
OSDK_SETTINGS = 2249
OSDK_ONLINE_PASS = 2268

REDIRECTOR_GET_SERVER_INSTANCE = 1
UTIL_FETCH_CONFIG = 1
UTIL_PING = 2
UTIL_SET_CLIENT_DATA = 3
UTIL_LOCALIZE_STRINGS = 4
UTIL_GET_TELEMETRY_SERVER = 5
UTIL_PREAUTH = 7
UTIL_POSTAUTH = 8
UTIL_USER_SETTINGS_LOAD = 10
UTIL_USER_SETTINGS_SAVE = 11
UTIL_USER_SETTINGS_LOAD_ALL = 12
UTIL_SET_CLIENT_METRICS = 22
UTIL_SET_CONNECTION_STATE = 23

AUTH_GET_ACCOUNT = 30
AUTH_HAS_ENTITLEMENT = 33
AUTH_LIST_ENTITLEMENTS = 32
AUTH_LIST_USER_ENTITLEMENTS_2 = 29
AUTH_GET_TOS_INFO = 42
AUTH_LOGOUT = 70
AUTH_XBOX_LOGIN = 170
AUTH_UPDATE_ACCOUNT = 20

AUTH2_LOGIN = 10

USER_UPDATE_HARDWARE_FLAGS = 8
USER_UPDATE_NETWORK_INFO = 20

STATS_GET_STAT_GROUP_LIST = 3
STATS_GET_KEY_SCOPES_MAP = 15
STATS_GET_PERIOD_IDS = 20

CENSUS_SUBSCRIBE = 1

CLUBS_GET_INVITATIONS = 1600
CLUBS_GET_COMPONENT_SETTINGS = 2600

MESSAGING_FETCH_MESSAGES = 2
MESSAGING_GET_MESSAGES = 5

ROOMS_SELECT_VIEW_UPDATES = 10

ASSOCIATION_GET_LISTS = 6

OSDK_SETTINGS_FETCH_SETTINGS = 1
OSDK_SETTINGS_FETCH_GROUPS = 2
OSDK_ONLINE_PASS_FETCH_GATES = 3

CARDHOUSE_LOGIN = 101
CARDHOUSE_LOGOUT = 102
CARDHOUSE_GAMER_SET_INFO = 103
CARDHOUSE_GAMER_GET_INFO = 104
CARDHOUSE_GET_CONFIG = 106
CARDHOUSE_GET_DECK_INFO = 301
CARDHOUSE_GET_SQUAD_LIST = 709

# The FIFA 14 client stub for command 3 constructs a
# Blaze::SponsoredEvents::URLResponse.  Its sole TDF member is the string URL.
SPONSORED_EVENTS_GET_EVENTS_URL = 3

REPLY = 1
NOTIFICATION = 2
ERROR_REPLY = 3

# Full Blaze value 0x00010864: error ordinal 1 in component 0x0864.
CARDHOUSE_ERR_NO_PLAYER_INFO_HEADER = 1

# Stock FIFA 14 PreAuth information observed from the supported Xbox build.
COMPONENT_IDS = [
    1,
    4,
    6,
    7,
    9,
    10,
    11,
    15,
    21,
    20,
    25,
    27,
    28,
    AUTHENTICATION2,
    2000,
    CARDHOUSE,
    OSDK_SETTINGS,
    OSDK_ONLINE_PASS,
    30720,
    30721,
    30722,
    30723,
    30725,
    30726,
]


@dataclass
class ClientState:
    connection_id: int
    peer: tuple[str, int]
    local_port: int
    gamertag: str = "OfflineFUT"
    xuid: int = 1
    email: str = "offline@localhost"
    authenticated: bool = False
    request_count: int = 0
    send_lock: threading.Lock = field(default_factory=threading.Lock)


class PersistentAccountStore:
    """Small local persistence layer for client-owned account preferences."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {
            # FIFA writes this exact value after completing its first-login UI.
            "user_settings": {"FirstTimeFlag": "0"},
            "account": {"OPTQ": 0, "OPTS": 0},
        }
        if path is not None and path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for section in ("user_settings", "account"):
                    value = loaded.get(section)
                    if isinstance(value, dict):
                        self.data[section].update(value)

    def _save_locked(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(self.data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def load_setting(self, key: str) -> str:
        with self.lock:
            return str(self.data["user_settings"].get(key, ""))

    def save_setting(self, key: str, value: str) -> None:
        with self.lock:
            self.data["user_settings"][key] = value
            self._save_locked()

    def save_account_preferences(self, optq: int, opts: int) -> None:
        with self.lock:
            self.data["account"].update({"OPTQ": optq, "OPTS": opts})
            self._save_locked()


def find_field(fields: list[Field], label: str) -> Field | None:
    for item in fields:
        if item.label == label:
            return item
        value = item.value
        if item.type == STRUCT and isinstance(value, list):
            nested = find_field(value, label)
            if nested is not None:
                return nested
        if item.type == UNION and isinstance(value, tuple):
            nested_field = value[1]
            if isinstance(nested_field, Field):
                if nested_field.label == label:
                    return nested_field
                if nested_field.type == STRUCT and isinstance(nested_field.value, list):
                    nested = find_field(nested_field.value, label)
                    if nested is not None:
                        return nested
    return None


def normal_header_size(header: bytes) -> int:
    """Return the ProtoFire header size for non-jumbo packets.

    The Xbox requests observed so far use the 12-byte header.  Context-bearing
    frames are accepted as well.  Jumbo payloads are rejected explicitly by
    the stream parser because they have not appeared in this title flow.
    """

    options = header[9] >> 4
    if options & 0x1:
        raise ValueError("Jumbo ProtoFire frames are not supported yet")
    size = 12
    if options & 0x2:
        size += 8 if options & 0x8 else 4
    return size


def response_frame(
    request: bytes,
    payload: bytes = b"",
    *,
    error: int = 0,
    message_type: int = REPLY,
) -> bytes:
    decoded = decode_frame(request[:12] + request[normal_header_size(request):])
    result = bytearray(
        encode_frame(
            decoded["component"],
            decoded["command"],
            error,
            message_type,
            decoded["message_number"],
            payload,
        )
    )
    # Preserve the request's local-user index.
    result[8] |= request[8] & 0x0F
    return bytes(result)


def notification_frame(component: int, command: int, payload: bytes) -> bytes:
    return encode_frame(component, command, 0, NOTIFICATION, 0, payload)


def empty_map(label: str) -> Field:
    return Field(label, MAP, (STRING, STRING, []))


class Fifa14Protocol:
    def __init__(
        self,
        advertise: str,
        core_port: int,
        logger: "Journal",
        identity_port: int = 18080,
        account_store: PersistentAccountStore | None = None,
    ):
        self.advertise = advertise
        self.core_port = core_port
        self.logger = logger
        self.identity_port = identity_port
        self.account_store = account_store or PersistentAccountStore()

    @property
    def identity_base(self) -> str:
        return f"http://{self.advertise}:{self.identity_port}"

    def fetch_config(self, request: bytes, fields: list[Field]) -> bytes:
        config_id = find_field(fields, "CFID")
        name = str(config_id.value) if config_id is not None else ""
        values: list[tuple[str, str]] = []
        if name == "IdentityParams":
            # The Xbox Authentication2 bootstrap appends this map to
            # nucleusConnect/connect/auth, then looks redirect_uri up again
            # while parsing the HTTP redirect.
            values = [
                ("client_id", "fifa14-xbox360-offline"),
                ("redirect_uri", f"{self.identity_base}/connect/redirect"),
            ]
        return response_frame(
            request,
            encode_fields(
                [Field("CONF", MAP, (STRING, STRING, values))]
            ),
        )

    def ping_site(self) -> list[Field]:
        return [
            Field("PSA", STRING, self.advertise),
            Field("PSP", INTEGER, 17502),
            Field("SNA", STRING, "ams"),
        ]

    def redirector(self, request: bytes) -> bytes:
        payload = encode_fields(
            [
                Field(
                    "ADDR",
                    UNION,
                    (
                        0,
                        Field(
                            "VALU",
                            STRUCT,
                            [
                                Field("HOST", STRING, self.advertise),
                                Field("IP", INTEGER, 0),
                                Field("PORT", INTEGER, self.core_port),
                            ],
                        ),
                    ),
                ),
                Field("SECU", INTEGER, 0),
                Field("XDNS", INTEGER, 0),
            ]
        )
        return response_frame(request, payload)

    def preauth(self, request: bytes) -> bytes:
        payload = encode_fields(
            [
                Field("ANON", INTEGER, 0),
                Field("ASRC", STRING, "300294"),
                Field("CIDS", LIST, (INTEGER, COMPONENT_IDS)),
                Field("CNGN", STRING, ""),
                Field(
                    "CONF",
                    STRUCT,
                    [
                        Field(
                            "CONF",
                            MAP,
                            (
                                STRING,
                                STRING,
                                [
                                    ("connIdleTimeout", "120s"),
                                    ("defaultRequestTimeout", "80s"),
                                    # Authentication2 reads these directly
                                    # from the PreAuth client-config map.
                                    ("nucleusConnect", self.identity_base),
                                    ("pingPeriod", "20s"),
                                    ("voipHeadsetUpdateRate", "1000"),
                                    # Keep the token audience on EA's historic
                                    # relying-party host while the HTTP target
                                    # itself is our local preservation server.
                                    ("xblTokenUrn", "http://accounts.ea.com"),
                                    ("xlspConnectionIdleTimeout", "300"),
                                ],
                            ),
                        )
                    ],
                ),
                # Stock console-auth bootstrap fields retained for parity with
                # the title's expected PreAuth schema.
                Field("EEFA", INTEGER, 1),
                Field("ESRC", STRING, "fifa-2014-xbl2"),
                Field("INST", STRING, "fifa-2014-xbl2"),
                Field("MINR", INTEGER, 0),
                Field("NASP", STRING, "cem_ea_id"),
                Field("PILD", STRING, "fifa-2014-xbl2"),
                Field("PLAT", STRING, "xbox360"),
                Field("PTAG", STRING, ""),
                Field(
                    "QOSS",
                    STRUCT,
                    [
                        Field("BWPS", STRUCT, self.ping_site()),
                        Field("LNP", INTEGER, 10),
                        Field(
                            "LTPS",
                            MAP,
                            (STRING, STRUCT, [("ams", self.ping_site())]),
                        ),
                        Field("SVID", INTEGER, 1161889797),
                    ],
                ),
                Field("RSRC", STRING, "300294"),
                Field("SVER", STRING, "Blaze 3.15.08.0 (CL# 1060080)"),
            ]
        )
        return response_frame(request, payload)

    def ping(self, request: bytes) -> bytes:
        return response_frame(
            request,
            encode_fields([Field("STIM", INTEGER, int(time.time()))]),
        )

    def postauth(self, request: bytes, state: ClientState) -> bytes:
        # FIFA 14's generated Blaze 3 PostAuthResponse contains four members,
        # in TDF-tag order: PSS, TELE, TICK and UROP.  PSS is a real embedded
        # struct even on Xbox 360 (where its PS3-specific values stay empty),
        # so emit it instead of relying on the client's default constructor.
        # The telemetry/ticker values mirror the working Zamboni legacy
        # implementation; those services are auxiliary, but non-zero ports
        # keep the title's post-auth setup on its normal success path.
        payload = encode_fields(
            [
                Field(
                    "PSS",
                    STRUCT,
                    [
                        Field("ADRS", STRING, ""),
                        Field("CSIG", BINARY, b""),
                        Field("OIDS", LIST, (STRING, [])),
                        Field("PJID", STRING, ""),
                        Field("PORT", INTEGER, 0),
                        Field("RPRT", INTEGER, 0),
                        Field("TIID", INTEGER, 0),
                    ],
                ),
                Field(
                    "TELE",
                    STRUCT,
                    [
                        Field("ADRS", STRING, self.advertise),
                        Field("ANON", INTEGER, 0),
                        Field("DISA", STRING, "disa"),
                        Field("FILT", STRING, "filt"),
                        Field("LOC", INTEGER, 1718765138),
                        Field("NOOK", STRING, "nook"),
                        Field("PORT", INTEGER, 6767),
                        Field("SDLY", INTEGER, 10),
                        Field("SESS", STRING, "id"),
                        Field("SKEY", STRING, "key"),
                        Field("SPCT", INTEGER, 10),
                        Field("STIM", STRING, "true"),
                    ],
                ),
                Field(
                    "TICK",
                    STRUCT,
                    [
                        Field("ADRS", STRING, self.advertise),
                        Field("PORT", INTEGER, 6776),
                        Field("SKEY", STRING, "key"),
                    ],
                ),
                Field(
                    "UROP",
                    STRUCT,
                    [
                        Field("TMOP", INTEGER, 0),
                        Field("UID", INTEGER, state.xuid),
                    ],
                ),
            ]
        )
        return response_frame(request, payload)

    def xbox_login(self, request: bytes, state: ClientState) -> list[bytes]:
        decoded = decode_frame(request)
        gamer = find_field(decoded["fields"], "GTAG")
        xuid = find_field(decoded["fields"], "XUID")
        mail = find_field(decoded["fields"], "MAIL")
        if gamer and isinstance(gamer.value, str) and gamer.value:
            state.gamertag = gamer.value
        if xuid and isinstance(xuid.value, int) and xuid.value:
            state.xuid = xuid.value
        if mail and isinstance(mail.value, str) and mail.value:
            state.email = mail.value
        state.authenticated = True

        now = int(time.time())
        persona = [
            Field("DSNM", STRING, state.gamertag),
            Field("LAST", INTEGER, now),
            Field("PID", INTEGER, state.xuid),
            Field("STAS", INTEGER, 2),
            Field("XREF", INTEGER, state.xuid),
            Field("XTYP", INTEGER, 1),
        ]
        session = [
            Field("BUID", INTEGER, state.xuid),
            Field("FRST", INTEGER, 1),
            Field("KEY", STRING, f"offline-{state.xuid:x}"),
            Field("LLOG", INTEGER, now),
            Field("MAIL", STRING, state.email),
            Field("PDTL", STRUCT, persona),
            Field("UID", INTEGER, state.xuid),
        ]
        login = encode_fields(
            [
                Field("AGUP", INTEGER, 0),
                Field("LDHT", STRING, ""),
                Field("NTOS", INTEGER, 0),
                Field("PRIV", STRING, ""),
                Field("SESS", STRUCT, session),
                Field("SPAM", INTEGER, 1),
                Field("THST", STRING, ""),
                Field("TSUI", STRING, ""),
                Field("TURI", STRING, ""),
            ]
        )

        user_identification = [
            Field("AID", INTEGER, state.xuid),
            Field("ALOC", INTEGER, 1718765138),
            Field("EXID", INTEGER, state.xuid),
            Field("ID", INTEGER, state.xuid),
            Field("NAME", STRING, state.gamertag),
        ]

        user_added = notification_frame(
            USER_SESSIONS,
            2,
            encode_fields(
                [
                    Field(
                        "DATA",
                        STRUCT,
                        [
                            Field("BPS", STRING, "ams"),
                            Field("CTY", STRING, "FR"),
                            Field("HWFG", INTEGER, 0),
                            Field("UATT", INTEGER, 0),
                        ],
                    ),
                    Field("USER", STRUCT, user_identification),
                ]
            ),
        )
        return [response_frame(request, login), user_added]

    def authentication2_login(
        self,
        request: bytes,
        state: ClientState,
        fields: list[Field],
    ) -> list[bytes]:
        """Complete FIFA 14's Nucleus-code login on component 35.

        This is a title-side ``Blaze::Authentication2`` component that is not
        present in the public Zamboni BlazeSDK.  Its exact LoginResponse field
        table was recovered from this supported FIFA 14 executable: ANON,
        SESS, SPAM and UNDR.  SESS is the standard Blaze Authentication
        SessionInfo structure.
        """

        external_id = find_field(fields, "EXTI")
        if external_id and isinstance(external_id.value, int) and external_id.value:
            state.xuid = external_id.value
        state.authenticated = True

        now = int(time.time())
        persona = [
            Field("DSNM", STRING, state.gamertag),
            Field("PID", INTEGER, state.xuid),
            # Authentication2::PersonaDetails in this FIFA executable has
            # exactly DSNM, PID and PLAT.  It is smaller than the similarly
            # named legacy Authentication::PersonaDetails structure.
            Field("PLAT", INTEGER, 1),  # ExternalSystemId::XBOX
        ]
        session = [
            Field("BUID", INTEGER, state.xuid),
            Field("FRST", INTEGER, 0),
            Field("KEY", STRING, f"offline-{state.xuid:x}"),
            Field("LLOG", INTEGER, now),
            Field("MAIL", STRING, state.email),
            Field("PDTL", STRUCT, persona),
            Field("UID", INTEGER, state.xuid),
        ]
        login = encode_fields(
            [
                Field("ANON", INTEGER, 0),
                Field("SESS", STRUCT, session),
                Field("SPAM", INTEGER, 1),
                Field("UNDR", INTEGER, 0),
            ]
        )

        user_identification = [
            Field("AID", INTEGER, state.xuid),
            Field("ALOC", INTEGER, 1718765138),
            Field("EXID", INTEGER, state.xuid),
            Field("ID", INTEGER, state.xuid),
            Field("NAME", STRING, state.gamertag),
        ]

        # FIFA 14 maps notification 8 to UserAuthenticated, but its payload is
        # the executable's 0x88-byte UserSessionLoginInfo, not the smaller
        # SUBS/BUID shape found in another legacy Blaze schema.  The native
        # callback at 0x82EE6150 consumes BUID, UID, XREF, DSNM and ALOC
        # directly; omitting them creates an anonymous second user object.
        user_authenticated = notification_frame(
            USER_SESSIONS,
            8,
            encode_fields(
                [
                    Field("ALOC", INTEGER, 1718765138),
                    Field("BUID", INTEGER, state.xuid),
                    Field("DSNM", STRING, state.gamertag),
                    Field("FRST", INTEGER, 0),
                    Field("KEY", STRING, f"offline-{state.xuid:x}"),
                    Field("LAST", INTEGER, 0),
                    Field("LLOG", INTEGER, now),
                    Field("MAIL", STRING, state.email),
                    Field("PID", INTEGER, state.xuid),
                    Field("PLAT", INTEGER, 1),
                    Field("UID", INTEGER, state.xuid),
                    Field("USTP", INTEGER, 1),
                    Field("XREF", INTEGER, state.xuid),
                ]
            ),
        )

        # The supported executable constructs a 0x188-byte NotifyUserAdded
        # containing DATA followed by USER.  DATA must be present even though
        # notification 1 subsequently refreshes the same extended-session
        # state; omitting it leaves the local User identity uncommitted.
        user_added = notification_frame(
            USER_SESSIONS,
            2,
            encode_fields(
                [
                    Field(
                        "DATA",
                        STRUCT,
                        [
                            Field("BPS", STRING, "ams"),
                            Field("CTY", STRING, "FR"),
                            Field("HWFG", INTEGER, 0),
                            Field("UATT", INTEGER, 0),
                        ],
                    ),
                    Field("USER", STRUCT, user_identification),
                ]
            ),
        )
        extended_data = notification_frame(
            USER_SESSIONS,
            1,
            encode_fields(
                [
                    Field(
                        "DATA",
                        STRUCT,
                        [
                            Field("BPS", STRING, "ams"),
                            Field("CTY", STRING, "FR"),
                            Field("HWFG", INTEGER, 0),
                            Field("UATT", INTEGER, 0),
                        ],
                    ),
                    # FIFA 14's retail UserSessionExtendedDataUpdate is a
                    # 0x140-byte structure with DATA, SUBS and USID.  SUBS is
                    # not part of the embedded extended-data object: it is a
                    # top-level boolean at +0x138.  Omitting it leaves the
                    # local user session present but unsubscribed.
                    Field("SUBS", INTEGER, 1),
                    Field("USID", INTEGER, state.xuid),
                ]
            ),
        )
        self.logger.event(
            "authentication2_login",
            connection=state.connection_id,
            external_id=state.xuid,
        )
        return [
            response_frame(request, login),
            user_authenticated,
            user_added,
            extended_data,
        ]

    def account(self, request: bytes, state: ClientState) -> bytes:
        payload = encode_fields(
            [
                Field("ANON", INTEGER, 0),
                Field("ASRC", STRING, "300294"),
                Field("CO", STRING, "FR"),
                Field("CTRY", STRING, "FR"),
                Field("DOB", STRING, "1980-01-01"),
                Field("DTCR", STRING, "2013-01-01"),
                Field("MAIL", STRING, state.email),
                Field("STAT", INTEGER, 1),
                Field("STAS", INTEGER, 1),
                Field("UID", INTEGER, state.xuid),
            ]
        )
        return response_frame(request, payload)

    def cardhouse_login(self, request: bytes) -> bytes:
        # Mirrors Zamboni's new LoginResponse(): value fields are emitted while
        # nullable NAME/ABBR/CVER remain absent, marking an uncreated club.
        payload = encode_fields(
            [
                Field("BNUS", INTEGER, 0),
                Field("DRRC", INTEGER, 0),
                Field("DRRL", INTEGER, 0),
                Field("DRRO", INTEGER, 0),
                Field("DRRW", INTEGER, 0),
                Field("RWRD", INTEGER, 0),
                Field("TNOW", INTEGER, 0),
                Field("TRBS", INTEGER, 0),
                Field("UID", INTEGER, 0),
            ]
        )
        return response_frame(request, payload)

    def cardhouse_no_player(self, request: bytes) -> bytes:
        return response_frame(
            request,
            b"",
            error=CARDHOUSE_ERR_NO_PLAYER_INFO_HEADER,
            message_type=ERROR_REPLY,
        )

    def sponsored_events_url(self, request: bytes) -> bytes:
        return response_frame(
            request,
            encode_fields(
                [Field("URL", STRING, f"{self.identity_base}/sponsored-events")]
            ),
        )

    def telemetry_server(self, request: bytes) -> bytes:
        """Return the Blaze 3 GetTelemetryServerResponse used by FIFA 14."""

        return response_frame(
            request,
            encode_fields(
                [
                    Field("ADRS", STRING, self.advertise),
                    Field("ANON", INTEGER, 0),
                    Field("DISA", STRING, "disa"),
                    Field("FILT", STRING, "filt"),
                    Field("LOC", INTEGER, 1718765138),
                    Field("NOOK", STRING, "nook"),
                    Field("PORT", INTEGER, 6767),
                    Field("SDLY", INTEGER, 10),
                    Field("SESS", STRING, "id"),
                    Field("SKEY", STRING, "key"),
                    Field("SPCT", INTEGER, 10),
                    Field("STIM", STRING, "true"),
                ]
            ),
        )

    def clubs_component_settings(self, request: bytes) -> bytes:
        # The nullable lists in Zamboni's default ClubsComponentSettings are
        # absent on the wire.  The six non-nullable scalars are still emitted.
        return response_frame(
            request,
            encode_fields(
                [
                    Field("CLDS", INTEGER, 0),
                    Field("MXEV", INTEGER, 0),
                    Field("MXRV", INTEGER, 0),
                    Field("PUHR", INTEGER, 0),
                    Field("SOVR", INTEGER, 0),
                    Field("STRT", INTEGER, 0),
                ]
            ),
        )

    def period_ids(self, request: bytes) -> bytes:
        # BlazeSDK PeriodIds contains fourteen non-nullable integer fields.
        labels = (
            "DBUF", "DHOU", "DLY", "DRET", "MBUF", "MDAY", "MHOU",
            "MLY", "MRET", "WBUF", "WDAY", "WHOU", "WLY", "WRET",
        )
        return response_frame(
            request,
            encode_fields([Field(label, INTEGER, 0) for label in labels]),
        )

    def osdk_settings(self, request: bytes) -> bytes:
        # ZamboniCommonComponents exposes one string setting used by the
        # legacy ticker.  Default nullable strings are deliberately omitted.
        setting = [
            Field("ID", STRING, "O_TKfilter"),
            Field("LOCF", INTEGER, 0),
            Field("TOGG", INTEGER, 0),
        ]
        return response_frame(
            request,
            encode_fields([Field("LSST", LIST, (STRUCT, [setting]))]),
        )

    def osdk_setting_groups(self, request: bytes) -> bytes:
        group = [
            Field("ID", STRING, "O_SG_TCKR"),
            Field("LSET", LIST, (STRING, ["O_TKfilter"])),
        ]
        return response_frame(
            request,
            encode_fields([Field("LGRP", LIST, (STRUCT, [group]))]),
        )

    def handle(self, request: bytes, state: ClientState) -> list[bytes]:
        decoded = decode_frame(request)
        route = (decoded["component"], decoded["command"])

        if route == (REDIRECTOR, REDIRECTOR_GET_SERVER_INSTANCE):
            return [self.redirector(request)]
        if route == (UTIL, UTIL_PREAUTH):
            return [self.preauth(request)]
        if route == (UTIL, UTIL_PING):
            return [self.ping(request)]
        if route == (UTIL, UTIL_POSTAUTH):
            return [self.postauth(request, state)]
        if route == (UTIL, UTIL_GET_TELEMETRY_SERVER):
            return [self.telemetry_server(request)]
        if route == (UTIL, UTIL_USER_SETTINGS_LOAD):
            key_field = find_field(decoded["fields"], "KEY")
            key = str(key_field.value) if key_field is not None else ""
            value = self.account_store.load_setting(key)
            self.logger.event(
                "user_setting_load",
                connection=state.connection_id,
                key=key,
                value=value,
            )
            return [
                response_frame(
                    request,
                    encode_fields([Field("DATA", STRING, value)]),
                )
            ]
        if route == (UTIL, UTIL_USER_SETTINGS_SAVE):
            key_field = find_field(decoded["fields"], "KEY")
            data_field = find_field(decoded["fields"], "DATA")
            key = str(key_field.value) if key_field is not None else ""
            value = str(data_field.value) if data_field is not None else ""
            if key:
                self.account_store.save_setting(key, value)
            self.logger.event(
                "user_setting_save",
                connection=state.connection_id,
                key=key,
                value=value,
            )
            return [response_frame(request)]
        if route == (UTIL, UTIL_FETCH_CONFIG):
            return [self.fetch_config(request, decoded["fields"])]
        if route == (UTIL, UTIL_USER_SETTINGS_LOAD_ALL):
            return [response_frame(request, encode_fields([empty_map("SMAP")]))]
        if route in {
            (UTIL, UTIL_SET_CLIENT_DATA),
            (UTIL, UTIL_SET_CLIENT_METRICS),
            (UTIL, UTIL_SET_CONNECTION_STATE),
            (USER_SESSIONS, USER_UPDATE_HARDWARE_FLAGS),
            (USER_SESSIONS, USER_UPDATE_NETWORK_INFO),
        }:
            return [response_frame(request)]
        if route == (UTIL, UTIL_LOCALIZE_STRINGS):
            # Returning no entries is sufficient for the bootstrap and avoids
            # making assumptions about the request's list schema.
            return [response_frame(request, encode_fields([empty_map("LOCL")]))]

        if route == (AUTHENTICATION, AUTH_XBOX_LOGIN):
            return self.xbox_login(request, state)
        if route == (AUTHENTICATION2, AUTH2_LOGIN):
            return self.authentication2_login(request, state, decoded["fields"])
        if route == (AUTHENTICATION, AUTH_LOGOUT):
            state.authenticated = False
            return [response_frame(request)]
        if route == (AUTHENTICATION, AUTH_GET_ACCOUNT):
            return [self.account(request, state)]
        if route == (AUTHENTICATION, AUTH_UPDATE_ACCOUNT):
            optq_field = find_field(decoded["fields"], "OPTQ")
            opts_field = find_field(decoded["fields"], "OPTS")
            optq = int(optq_field.value) if optq_field is not None else 0
            opts = int(opts_field.value) if opts_field is not None else 0
            self.account_store.save_account_preferences(optq, opts)
            self.logger.event(
                "account_preferences_save",
                connection=state.connection_id,
                optq=optq,
                opts=opts,
            )
            return [response_frame(request)]
        if route in {
            (AUTHENTICATION, AUTH_HAS_ENTITLEMENT),
            (AUTHENTICATION, AUTH_LIST_ENTITLEMENTS),
            (AUTHENTICATION, AUTH_LIST_USER_ENTITLEMENTS_2),
            (AUTHENTICATION, AUTH_GET_TOS_INFO),
        }:
            return [response_frame(request)]

        if route == (CARDHOUSE, CARDHOUSE_LOGIN):
            return [self.cardhouse_login(request)]
        if route == (CARDHOUSE, CARDHOUSE_GAMER_GET_INFO):
            return [self.cardhouse_no_player(request)]
        if route in {
            (CARDHOUSE, CARDHOUSE_LOGOUT),
            (CARDHOUSE, CARDHOUSE_GAMER_SET_INFO),
            (CARDHOUSE, CARDHOUSE_GET_CONFIG),
            (CARDHOUSE, CARDHOUSE_GET_DECK_INFO),
            (CARDHOUSE, CARDHOUSE_GET_SQUAD_LIST),
        }:
            return [response_frame(request)]

        if route == (SPONSORED_EVENTS, SPONSORED_EVENTS_GET_EVENTS_URL):
            return [self.sponsored_events_url(request)]

        if route == (MESSAGING, MESSAGING_FETCH_MESSAGES):
            return [
                response_frame(
                    request,
                    encode_fields([Field("MCNT", INTEGER, 0)]),
                )
            ]
        if route == (MESSAGING, MESSAGING_GET_MESSAGES):
            return [response_frame(request)]
        if route == (ASSOCIATION_LISTS, ASSOCIATION_GET_LISTS):
            return [
                response_frame(
                    request,
                    encode_fields([Field("LMAP", LIST, (STRUCT, []))]),
                )
            ]
        if route == (CLUBS, CLUBS_GET_COMPONENT_SETTINGS):
            return [self.clubs_component_settings(request)]
        if route == (CLUBS, CLUBS_GET_INVITATIONS):
            return [
                response_frame(
                    request,
                    encode_fields([Field("CIST", LIST, (STRUCT, []))]),
                )
            ]
        if route == (STATS, STATS_GET_KEY_SCOPES_MAP):
            return [
                response_frame(
                    request,
                    encode_fields([Field("KSIT", MAP, (STRING, STRUCT, []))]),
                )
            ]
        if route == (STATS, STATS_GET_STAT_GROUP_LIST):
            return [
                response_frame(
                    request,
                    encode_fields([Field("GRPS", LIST, (STRUCT, []))]),
                )
            ]
        if route == (STATS, STATS_GET_PERIOD_IDS):
            return [self.period_ids(request)]
        if route in {
            (CENSUS_DATA, CENSUS_SUBSCRIBE),
            (ROOMS, ROOMS_SELECT_VIEW_UPDATES),
        }:
            return [response_frame(request)]
        if route == (OSDK_SETTINGS, OSDK_SETTINGS_FETCH_SETTINGS):
            return [self.osdk_settings(request)]
        if route == (OSDK_SETTINGS, OSDK_SETTINGS_FETCH_GROUPS):
            return [self.osdk_setting_groups(request)]
        if route == (OSDK_ONLINE_PASS, OSDK_ONLINE_PASS_FETCH_GATES):
            return [
                response_frame(
                    request,
                    encode_fields([Field("LIST", LIST, (STRUCT, []))]),
                )
            ]

        self.logger.event(
            "unknown_route",
            connection=state.connection_id,
            component=route[0],
            command=route[1],
        )
        return [response_frame(request)]


class IdentityHttpService:
    """Minimal local Nucleus OAuth endpoint used by Authentication2."""

    def __init__(
        self,
        listen: str,
        port: int,
        advertise: str,
        journal: "Journal",
    ):
        self.listen = listen
        self.port = port
        self.advertise = advertise
        self.journal = journal
        self.server: http.server.ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def public_base(self) -> str:
        port = self.port if self.server is None else self.server.server_address[1]
        return f"http://{self.advertise}:{port}"

    def start(self) -> None:
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def reply(
                self,
                status: int,
                body: bytes = b"",
                headers: dict[str, str] | None = None,
            ) -> None:
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD" and body:
                    self.wfile.write(body)

            def serve_identity(self) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                owner.journal.event(
                    "identity_http_request",
                    peer=self.client_address[0],
                    method=self.command,
                    path=parsed.path,
                    query_keys=sorted(urllib.parse.parse_qs(parsed.query).keys()),
                )
                if parsed.path == "/connect/auth":
                    location = (
                        f"{owner.public_base}/connect/redirect"
                        "?code=offline-fifa14-auth"
                    )
                    owner.journal.event(
                        "identity_http_redirect",
                        peer=self.client_address[0],
                        location=location,
                    )
                    self.reply(302, headers={"Location": location})
                    return
                if parsed.path == "/connect/redirect":
                    self.reply(
                        200,
                        b'{"code":"offline-fifa14-auth"}\n',
                        {"Content-Type": "application/json"},
                    )
                    return
                if parsed.path == "/health":
                    self.reply(200, b"ok\n", {"Content-Type": "text/plain"})
                    return
                if parsed.path == "/sponsored-events":
                    # The title only needs a valid non-empty URL during the
                    # global online bootstrap.  Keep the local target benign
                    # in case a menu later opens it in the embedded browser.
                    self.reply(204)
                    return
                self.reply(404, b"not found\n", {"Content-Type": "text/plain"})

            do_GET = serve_identity
            do_HEAD = serve_identity
            do_POST = serve_identity

        self.server = http.server.ThreadingHTTPServer((self.listen, self.port), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name=f"identity-http-{self.server.server_address[1]}",
            daemon=True,
        )
        self.thread.start()
        self.journal.event(
            "identity_http_listening",
            address=self.listen,
            port=self.server.server_address[1],
            public_base=self.public_base,
        )

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=1.0)


class Journal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.Lock()

    def event(self, kind: str, **values: Any) -> None:
        record = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": kind,
            **values,
        }
        line = json.dumps(record, sort_keys=True, ensure_ascii=False)
        with self.lock:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        print(line, flush=True)

    def frame(self, direction: str, state: ClientState, raw: bytes) -> None:
        try:
            decoded = json_value(decode_frame(raw))
            self.event(
                "frame",
                direction=direction,
                connection=state.connection_id,
                peer=f"{state.peer[0]}:{state.peer[1]}",
                local_port=state.local_port,
                frame=decoded,
                hex=raw.hex().upper(),
            )
        except Exception as error:
            self.event(
                "frame_decode_error",
                direction=direction,
                connection=state.connection_id,
                error=str(error),
                hex=raw.hex().upper(),
            )


class BlazeService:
    def __init__(
        self,
        listen: str,
        ports: list[int],
        protocol: Fifa14Protocol,
        journal: Journal,
    ):
        self.listen = listen
        self.ports = ports
        self.protocol = protocol
        self.journal = journal
        self.stop_event = threading.Event()
        self.listeners: list[socket.socket] = []
        self.threads: list[threading.Thread] = []
        self.connection_counter = 0
        self.counter_lock = threading.Lock()

    def next_connection_id(self) -> int:
        with self.counter_lock:
            self.connection_counter += 1
            return self.connection_counter

    def start(self) -> None:
        for port in self.ports:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.listen, port))
            listener.listen(16)
            listener.settimeout(0.5)
            self.listeners.append(listener)
            thread = threading.Thread(
                target=self.accept_loop,
                args=(listener, port),
                name=f"blaze-listen-{port}",
                daemon=True,
            )
            thread.start()
            self.threads.append(thread)
            self.journal.event("listening", address=self.listen, port=port)

    def stop(self) -> None:
        self.stop_event.set()
        for listener in self.listeners:
            try:
                listener.close()
            except OSError:
                pass
        for thread in self.threads:
            thread.join(timeout=1.0)

    def accept_loop(self, listener: socket.socket, port: int) -> None:
        while not self.stop_event.is_set():
            try:
                client, peer = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            state = ClientState(self.next_connection_id(), peer, port)
            thread = threading.Thread(
                target=self.client_loop,
                args=(client, state),
                name=f"blaze-client-{state.connection_id}",
                daemon=True,
            )
            thread.start()
            self.threads.append(thread)

    def client_loop(self, client: socket.socket, state: ClientState) -> None:
        self.journal.event(
            "connected",
            connection=state.connection_id,
            peer=f"{state.peer[0]}:{state.peer[1]}",
            local_port=state.local_port,
        )
        client.settimeout(0.5)
        buffer = bytearray()
        try:
            while not self.stop_event.is_set():
                try:
                    block = client.recv(65536)
                except socket.timeout:
                    continue
                if not block:
                    return
                buffer.extend(block)

                # A TLS ClientHello starts with a TLS record byte, not a Blaze
                # payload length.  Record it explicitly so routing/certificate
                # work is not confused with malformed ProtoFire traffic.
                if len(buffer) >= 3 and buffer[0] in (0x14, 0x15, 0x16, 0x17):
                    self.journal.event(
                        "tls_client_hello",
                        connection=state.connection_id,
                        local_port=state.local_port,
                        prefix=bytes(buffer[:64]).hex().upper(),
                    )
                    return

                while len(buffer) >= 12:
                    header_size = normal_header_size(buffer)
                    if len(buffer) < header_size:
                        break
                    payload_size = int.from_bytes(buffer[0:2], "big")
                    frame_size = header_size + payload_size
                    if frame_size > 2 * 1024 * 1024:
                        raise ValueError(f"Implausible Blaze frame size {frame_size}")
                    if len(buffer) < frame_size:
                        break
                    wire = bytes(buffer[:frame_size])
                    del buffer[:frame_size]
                    # The current decoder supports only the common 12-byte
                    # header.  Context bytes are removed for payload decoding.
                    request = wire if header_size == 12 else wire[:12] + wire[header_size:]
                    state.request_count += 1
                    self.journal.frame("request", state, request)
                    for response in self.protocol.handle(request, state):
                        self.journal.frame("response", state, response)
                        with state.send_lock:
                            client.sendall(response)
        except Exception as error:
            self.journal.event(
                "connection_error",
                connection=state.connection_id,
                error=f"{type(error).__name__}: {error}",
                buffered=bytes(buffer).hex().upper(),
            )
        finally:
            try:
                client.close()
            except OSError:
                pass
            self.journal.event(
                "disconnected",
                connection=state.connection_id,
                requests=state.request_count,
            )


def parse_ports(value: str) -> list[int]:
    result = []
    for item in value.split(","):
        port = int(item.strip(), 0)
        if not 1 <= port <= 65535:
            raise argparse.ArgumentTypeError(f"Invalid TCP port {port}")
        if port not in result:
            result.append(port)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--advertise", required=True)
    parser.add_argument("--core-port", type=int, default=10041)
    parser.add_argument("--identity-port", type=int, default=18080)
    parser.add_argument(
        "--ports",
        type=parse_ports,
        default=parse_ports("10041,42124,42126,42127"),
        help="comma-separated Blaze TCP listener ports",
    )
    parser.add_argument(
        "--journal",
        type=Path,
        default=REPOSITORY / "runtime" / "blaze-server.jsonl",
    )
    parser.add_argument(
        "--account-state",
        type=Path,
        default=REPOSITORY / "runtime" / "local-account.json",
    )
    args = parser.parse_args()

    journal = Journal(args.journal)
    account_store = PersistentAccountStore(args.account_state)
    protocol = Fifa14Protocol(
        args.advertise,
        args.core_port,
        journal,
        identity_port=args.identity_port,
        account_store=account_store,
    )
    service = BlazeService(args.listen, args.ports, protocol, journal)
    identity = IdentityHttpService(
        args.listen,
        args.identity_port,
        args.advertise,
        journal,
    )

    def stop(_signum: int, _frame: object) -> None:
        service.stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    service.start()
    identity.start()
    journal.event(
        "ready",
        advertise=args.advertise,
        core_port=args.core_port,
        identity_base=protocol.identity_base,
        components=COMPONENT_IDS,
    )
    try:
        while not service.stop_event.wait(0.5):
            pass
    finally:
        identity.stop()
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
