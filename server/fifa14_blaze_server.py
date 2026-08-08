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
import ssl
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

# Minimal configuration accepted by the retail Xbox 360 FutCfg parser.
#
# The schema and the required non-zero fields were recovered directly from
# default.xex (0x827F68B8 -> 0x827F07B0 -> 0x827EBFA8).  In particular:
#
# * cfgVersion populates FutCfg +0x140;
# * minorVersion populates +0x11C;
# * the matching revision/Language dimeUniqueId populates +0x120;
# * key/dimeUniqueId populates +0x148.
#
# All four values are checked before the native async completion is allowed to
# report success.  bootString names the title's own FUT server-call frontend;
# it does not skip the subsequent CardHouse/Blaze session.
FUT_BOOT_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<FutCfg>
  <cfgVersion>1</cfgVersion>
  <futDlc>
    <fut12>
      <minorVersion>1</minorVersion>
      <bootString>fut12</bootString>
      <futNotAvailable>0</futNotAvailable>
      <revision>
        <futSubVersion>1</futSubVersion>
        <Language>
          <dimeUniqueId>1</dimeUniqueId>
          <size>1</size>
        </Language>
      </revision>
      <key>
        <dimeUniqueId>2</dimeUniqueId>
        <futKeyType>0</futKeyType>
      </key>
    </fut12>
  </futDlc>
</FutCfg>
"""

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
    # The four-byte locale the client presents in PreAuth, e.g. "frFR".  The
    # EASW gate compares its own locale against a downloaded allow-list before
    # it will even build its authentication request, so echoing back exactly
    # what this console reported is what opens that gate.
    locale: str = ""
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
            "identity": {"persona_id": 1_000_001, "persona_name": "OfflineFUT"},
        }
        if path is not None and path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for section in ("user_settings", "account", "identity"):
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

    def load_all_settings(self) -> list[tuple[str, str]]:
        with self.lock:
            return sorted(
                (str(key), str(value))
                for key, value in self.data["user_settings"].items()
            )

    def save_setting(self, key: str, value: str) -> None:
        with self.lock:
            self.data["user_settings"][key] = value
            self._save_locked()

    def save_account_preferences(self, optq: int, opts: int) -> None:
        with self.lock:
            self.data["account"].update({"OPTQ": optq, "OPTS": opts})
            self._save_locked()

    def save_identity(self, persona_id: int, persona_name: str) -> None:
        with self.lock:
            self.data["identity"].update(
                {"persona_id": int(persona_id), "persona_name": str(persona_name)}
            )
            self._save_locked()

    def load_identity(self) -> tuple[int, str]:
        with self.lock:
            identity = self.data["identity"]
            return int(identity["persona_id"]), str(identity["persona_name"])


# CardsDLL formats its authentication request against OSDK_EASW_AUTH_URL.
# The PC build posts JSON to ``/v2/authenticationNucleusPersona``; this Xbox
# build posts a form to ``/authentication360`` with a ``version`` query and
# its own EASW-* signature headers.  Accept both.
EASW_AUTH_PATHS = ("/authentication360", "/v2/authenticationNucleusPersona")

# The FUT HTTP surface, keyed by exact path.  Every body here is the response
# the corresponding FIFA 14 parser treats as "nothing yet": empty collections
# and absent optional members, so the client keeps its own zeroed defaults
# instead of being handed a fabricated club, inventory, currency or squad.  The
# paths and the parser analysis behind them come from a working local
# implementation of this same title.
FUT_ROUTES: dict[str, bytes] = {
    "/ut/delete/auth": b"{}",
    # The header calls the currency "FIFA coins", and CardsDLL's JSON member
    # table carries `coins` alongside GetCoinsBalance and RefreshUserCredit --
    # so the member is very likely `coins`, not `credits`.
    #
    # Both are sent, at the top level. The earlier attempt wrapped them in
    # {"userInfo":{...}} and the header read -842150451, which is 0xCDCDCDCD:
    # the parser did not recognise the shape, never wrote the field, and the
    # header printed uninitialised memory. An unknown *wrapper* breaks the
    # parse; an unknown sibling member at the top level is simply skipped.
    "/ut/game/fifa14/user": b'{"coins":50000,"credits":50000}',
    "/ut/game/fifa14/userdata": b"{}",
    # A club with no coins cannot buy a pack or bid on anything, so the store
    # and market screens render but do nothing. Give the founding club a
    # working balance.
    # The parser reads a currencies array, not a "credits" number. Sending the
    # latter left the balance at whatever the response constructor held, which
    # is what showed up in the club header as a negative figure.
    # Same two spellings here, for the same reason.
    "/ut/game/fifa14/user/credits": (
        b'{"coins":50000,"credits":50000,'
        b'"currencies":[{"name":"COINS","funds":50000,"finalFunds":50000},'
        b'{"name":"POINTS","funds":0,"finalFunds":0}],'
        b'"unopenedPacks":{"preOrderPacks":0,"recoveredPacks":0}}'
    ),
    "/ut/game/fifa14/user/historical": b"{}",
    "/ut/game/fifa14/match": b"{}",
    "/ut/game/fifa14/match/ready": b"{}",
    "/ut/game/fifa14/match/end": b"{}",
    "/ut/game/fifa14/clientdata/tutorialpopups": b"{}",
    "/ut/game/fifa14/clientdata/userHubData": b"{}",
    # Trade pile, watch list and club capacity. Zero here means every add is
    # refused as "pile full"; these are the retail defaults the PC revival
    # carries.
    "/ut/game/fifa14/clientdata/pileSize": (
        b'{"entries":[{"key":2,"value":20000},{"key":3,"value":20000},'
        b'{"key":4,"value":20000}]}'
    ),
    "/ut/game/fifa14/clientdata/totw": b"{}",
    "/ut/game/fifa14/clientdata/managerquest": b'{"entries":[]}',
    "/ut/game/fifa14/eventfeed": b"{}",
    # The My Club tile reads clubPlayers from here; an empty object is why it
    # showed zero cards while the club held them.
    "/ut/game/fifa14/hub": b'{"auctionCount":0,"clubPlayers":92}',
    "/ut/game/fifa14/leaderboards/options": b"{}",
    "/ut/game/fifa14/utStats": b"{}",
    # FutGetClubUsersServerResponse reads a `user` array, singular, whose
    # entries carry persona/personaId/public. `users` matched nothing.
    "/ut/game/fifa14/clubUser": (
        b'{"user":[{"persona":"Fondateur FUT","personaId":0,"public":false}]}'
    ),
    # The club-creation screen PUTs the chosen name here and treats a 404 as a
    # connection failure -- "une erreur s'est produite lors de la connexion a
    # FIFA 14 Ultimate Team".  The PC revival never saw this route because the
    # PC client posts its club through clubUser instead; this one is the Xbox
    # client's own.  An empty object acknowledges the rename without inventing
    # club, crest, kit or inventory state.
    "/ut/game/fifa14/user/club": b"{}",
    "/ut/game/fifa14/club/stats/staff": b'{"bonus":[]}',
    "/ut/game/fifa14/club/stats/year": b'{"entries":[]}',
    "/ut/game/fifa14/club/stats/consumables": b'{"entries":[]}',
    "/ut/game/fifa14/club/stats/newcards": b'{"entries":[]}',
    "/ut/game/fifa14/item": b'{"itemData":[]}',
    "/ut/delete/game/fifa14/item": b"{}",
    # The market parser reads all three members; omitting the count and the
    # duplicate list leaves two of them at whatever the constructor held.
    # Polled right after a market search to refresh the state of the bids you
    # have out. A 404 here raises an error popup over a search that otherwise
    # worked. Nothing is bid on yet, so the list is empty.
    "/ut/game/fifa14/trade/status": (
        b'{"auctionInfo":[],"duplicateItemIdList":[],"total":0}'
    ),
    "/ut/game/fifa14/tradePile": (
        b'{"auctionInfo":[],"duplicateItemIdList":[],"total":0}'
    ),
    "/ut/game/fifa14/watchlist": (
        b'{"auctionInfo":[],"duplicateItemIdList":[],"total":0}'
    ),
    # Acknowledges the squad the client saves at the end of club creation; the
    # PC revival answers with the same id it was asked to store.
    "/ut/game/fifa14/squad/1": b'{"id":1}',
    # Entering Saison Joueur Solo asks for this list; a 404 surfaces as "un
    # probleme de communication est survenu avec les serveurs FIFA Ultimate
    # Team".  The PC revival carries the same empty-seasons shape.
    "/ut/game/fifa14/season/list": b'{"seasons":[]}',
    # The season screen asks for the user's own season state straight after the
    # list.  An empty object leaves the native response at its constructor
    # defaults -- no division, no points, no record invented.
    "/ut/game/fifa14/season/user": b"{}",
    "/ut/game/fifa14/tournament/list": b'{"tournament":[]}',
    "/ut/game/fifa14/tournament/user/list": b'{"tournamentId":[]}',
    # A visible-but-invalid single entry keeps the store screen constructible
    # without offering anything purchasable.
    "/ut/game/fifa14/store": b'{"purchase":[],"timestamp":2147483647}',
    # One real, buyable gold pack rather than a deliberately invalid entry:
    # the store screen now has something to sell, which is what a club with a
    # balance is for. Same record the PC revival serves.
    "/ut/game/fifa14/store/purchasegroup/all": (
        b'{"purchase":[{"id":304,"assetId":3,"actionType":"CREATEPACK",'
        b'"packType":"CARDPACK","description":"FUT_STORE_PACK_304_DESC",'
        b'"displayGroup":{"priority":3,"value":"gold"},"displayGroupAssetId":3,'
        b'"displayGroupUseDefaultImage":true,"useDefaultImage":true,'
        b'"isPremium":true,"dealType":"REGULAR","saleType":"NONE",'
        b'"state":"active","visible":1,"sortPriority":1,'
        b'"currencies":[{"name":"COINS","funds":7500,"finalFunds":7500}]}],'
        b'"timestamp":2147483647}'
    ),
    "/ut/v2/game/fifa14/store/transaction": b'{"state":"NOTRANSACTION"}',
}
EASW_AUTH_PATH = EASW_AUTH_PATHS[0]
ICEBREAKER_PACK_LIST = Path(__file__).resolve().parent / "icebreakerpacklist.json"

# What a quick sell pays when the request does not say which card went. The
# real discardValue travels on the item; this is the floor.
SELL_PRICE_FALLBACK = 200


def with_balance(payload: bytes, coins: int) -> bytes:
    """Add the coin total to a response that is known to carry one.

    Do not call this on every FUT route. Adding the total to all of them froze
    the login: the fan-out stopped dead at clientdata/tutorialpopups and went no
    further. So an unrecognised sibling is *not* universally skipped -- some of
    these parsers reject an object carrying members they do not know, and the
    login step waiting on that response never completes.

    Use it only where a balance genuinely belongs: the user and credits
    responses, quick sell, market searches, trade state, and pack purchases.

    Original note, still true of those responses:

    The club header is refreshed from whichever response last carried a
    balance, and these response constructors zero their fields before parsing.
    So a reply that omits the total does not leave the header alone -- it sets
    it to zero. That is why the balance read 0 from the moment FUT started
    while the server held 50000: some response in the login fan-out was
    zeroing it, and finding which one by elimination would have cost a relaunch
    per candidate.

    Naming the total three ways is deliberate. `totalCredits` is the member in
    CardsDLL's own JSON table; `credits` and `coins` ride along because an
    unrecognised sibling at the top level is skipped. A wrapper is not -- when
    these were nested under `userInfo` the header printed 0xCDCDCDCD.
    """
    if not payload.startswith(b"{"):
        return payload
    try:
        document = json.loads(payload)
    except ValueError:
        return payload
    if not isinstance(document, dict):
        return payload
    document.setdefault("credits", coins)
    document.setdefault("totalCredits", coins)
    document.setdefault("coins", coins)
    return json.dumps(document, separators=(",", ":")).encode()

# The club's cards. Built once at import from the icebreaker packs this build
# ships, so every screen that asks about the club sees the same inventory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fut_inventory import (  # noqa: E402
    GOLD_PACK_ID,
    CardActions,
    ClubSave,
    CardCatalogue,
    ClubInventory,
    PackShop,
    Wallet,
    active_tournaments_response,
    season_user_response,
    seasons_response,
    club_stats_response,
    consumables_response,
    hub_response,
    store_catalogue,
    totw_index_with_squad,
    totw_response,
    tournaments_response,
)

CLUB_INVENTORY = ClubInventory()
CARD_CATALOGUE = CardCatalogue()
WALLET = Wallet()
PACK_SHOP = PackShop(CARD_CATALOGUE, WALLET)
CARD_ACTIONS = CardActions(PACK_SHOP, WALLET, CLUB_INVENTORY)

# Entering FUT needs a relaunch, so without this every session started from the
# icebreaker packs again: the club counter back to 92, the pack you opened
# gone, the coins reset.
CLUB_SAVE = ClubSave()
CLUB_SAVE.load(CLUB_INVENTORY, WALLET, CARD_ACTIONS)
CLUB_NAME = "Fondateur FUT"

EASW_TOKEN = "LOCAL-FIFA14-EASW-TOKEN"
EASW_SESSION = "LOCAL-FIFA14-EASW-SESSION"

REQUEST_BODY_PREVIEW_LIMIT = 4096


def request_body_preview(body: bytes) -> str | None:
    """Return a bounded, journal-safe rendering of a client request body.

    The retail Xbox client posts small JSON documents whose exact schema is
    the evidence needed to model a response.  Binary or oversized bodies are
    summarised instead of being decoded so the journal stays readable.
    """
    if not body:
        return None
    truncated = body[:REQUEST_BODY_PREVIEW_LIMIT]
    try:
        text = truncated.decode("utf-8")
    except UnicodeDecodeError:
        return f"<{len(body)} non-utf8 bytes> {truncated[:64].hex().upper()}"
    if len(body) > len(truncated):
        return text + f"...<truncated, {len(body)} bytes total>"
    return text


def auth_request_identity(body: bytes) -> tuple[int, str] | None:
    """Return the persona the FUT auth request itself presents, when present.

    The retail Xbox client posts its own Nucleus id and profile display name
    to ``pow/auth``.  Answering ``accountinfo`` with a different persona name
    makes the client describe an account it never asked about, so prefer the
    identity carried by the request over any stored placeholder.
    """
    if not body:
        return None
    try:
        document = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    nucleus = document.get("nuc")
    display_name = document.get("nucleusPersonaDisplayName")
    if not isinstance(nucleus, int) or isinstance(nucleus, bool) or nucleus <= 0:
        return None
    if not isinstance(display_name, str) or not display_name:
        return None
    persona = document.get("nucleusPersonaId")
    # A zero persona id means the client has no FUT persona yet and expects
    # the server to name one.  Keep it tied to the Nucleus id in that case.
    if isinstance(persona, int) and not isinstance(persona, bool) and persona > 0:
        return persona, display_name
    return nucleus, display_name


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

    def fetch_config(
        self,
        request: bytes,
        fields: list[Field],
        state: ClientState | None = None,
    ) -> bytes:
        config_id = find_field(fields, "CFID")
        name = str(config_id.value) if config_id is not None else ""
        values: list[tuple[str, str]] = []
        # Which configuration maps the client asks for, and when, is the only
        # signal that says whether it ever reached the point of loading
        # CardsDLL: DLC_USE_REAL_DLL_LOAD lives in OSDK_CLIENT, and without
        # that fetch the DLC wrapper reports success without mapping anything.
        # A session that never asks looks identical to one that asked and was
        # answered badly, so record the request itself.
        journal_fetch = lambda served: self.logger.event(  # noqa: E731
            "config_fetch",
            connection=state.connection_id if state is not None else None,
            name=name,
            values=dict(served),
        )
        if name == "OSDK_CORE":
            # CardsDLL reads its EASW settings from this map, not OSDK_CLIENT.
            # Two of them decide whether it ever speaks: with
            # OSDK_EASW_ALLOWED_LOCALES absent the native gate falls back to
            # "----" and refuses to build the authentication request at all,
            # and without OSDK_EASW_AUTH_URL it has nowhere to send it.  The
            # allow-list echoes the locale this console reported in PreAuth,
            # so the gate matches without guessing a region.
            fut_base = f"{self.identity_base}/"
            locale = (state.locale if state is not None else "") or "enUS"
            values = [
                # EA Sports Football Club. powdllzf names its own endpoints --
                # pal.gt.easfc.ea.com:8094 for the session and
                # content.lt.easfc.ea.com:8080 for the catalogue -- and neither
                # is among the hostnames the launch patch redirects, which is
                # why the header reads "EAS FC non connecte". These keys are
                # what the module reads in preference to those defaults; the
                # retail values give the host:port form.
                #
                # POWService::PowBlazeDisconnected says the session itself is a
                # Blaze connection, not HTTP, so the session URL points at the
                # Blaze core port and the content URL at the identity server.
                ("ONLINE/POW_CUSTOMURL", f"{self.advertise}:{self.core_port}"),
                ("ONLINE/POW_CUSTOMCONTENTURL", self.identity_base),
                ("FIFA_POW_URL", f"{self.advertise}:{self.core_port}"),
                ("FIFA_POW_CONTENT_SERVER_URL", self.identity_base),
                ("FIFA_POW_NUCLEUS_PROXY_URL", self.identity_base),
                ("FUT_ENABLE_MENU", "1"),
                ("OSDK_EASW_ALLOWED_LOCALES", locale),
                ("OSDK_EASW_AUTH_URL", self.identity_base),
                ("FUTBOOTCFGFILE_URL", f"{self.identity_base}/futBoot.xml"),
                # Left unset, CardsDLL falls back to the retired
                # easw.easports.com:8099 and pg.fifa13.test... hosts, both of
                # which are still present as literals in the shipped DLL.
                ("FUT_RS4_BASE_URL", fut_base),
                ("FUTDYNAMICMESSAGES_URL_BASE", self.identity_base),
            ]
        elif name == "IdentityParams":
            # The Xbox Authentication2 bootstrap appends this map to
            # nucleusConnect/connect/auth, then looks redirect_uri up again
            # while parsing the HTTP redirect.
            values = [
                ("client_id", "fifa14-xbox360-offline"),
                ("redirect_uri", f"{self.identity_base}/connect/redirect"),
            ]
        elif name == "OSDK_CLIENT":
            # The retail client asks the OSDK configuration service whether
            # an obsolete EASW asset refresh must complete before EnterFUT2.
            # With the original content host gone, leaving the section empty
            # keeps FutCfg uninitialised (native status 0x0B) and the FUT
            # loader never starts.  This is the title's own offline/no-update
            # switch: it bypasses only the dead asset patcher, while the real
            # Blaze login and subsequent CardHouse session remain mandatory.
            #
            # DLC_USE_REAL_DLL_LOAD is equally important on retail builds.
            # When it is absent/zero, the DLC wrapper reports success without
            # calling the XEX loader.  FUT then waits forever because
            # CardsDLLzf.xex.dll was never mapped and cannot open CardHouse.
            fut_base = f"{self.identity_base}/"
            values = [
                ("ONLINE/NO_ASSET_UPDATE", "1"),
                ("DLC_USE_REAL_DLL_LOAD", "1"),
                # Exact CardsDLLzf.xex.dll configuration names recovered from
                # the active Xbox 360 TU3 image.  The platform formatter uses
                # the literal suffix "XBox360" on this build.
                ("FUTBOOTCFGFILE_URL", f"{self.identity_base}/futBoot.xml"),
                ("FUT_URI", fut_base),
                ("FUT_RS4_BASE_URL", fut_base),
                # These are present in the verified PC flow before CardsDLL
                # accepts its static localization assets and naturally starts
                # the Authentication WebSession.  CardsDLLzf.xex.dll contains
                # the directed-environment key verbatim; the deployment
                # language is consumed by the FIFA-side Cards bridge.
                ("CARDS/DIRECTED_BLAZEENV", "prod"),
                ("FCC/FUT_DEPLOY_LANGUAGE", "en_US"),
                ("FUT/SINGLE_BASEURL_XBox360", fut_base),
                ("FUT_RS4_URL_XBox360", fut_base),
                ("FUT_RS4_APIURL_XBox360", fut_base),
                ("FUT/MODULE_BASEURL_XBox360", fut_base),
                ("FUTDYNAMICMESSAGES_URL_BASE", self.identity_base),
                ("FUTDYNAMICMESSAGES_URL_GET_MESSAGES", "/messages"),
                ("ONLINE/FUTDYNAMICMESSAGES_TUTORIAL_MSG_URL", "/tutorials"),
                ("FUTDYNAMICMESSAGES_REQUEST_TIMEOUT", "5000"),
                ("FUTDYNAMICMESSAGES_REFRESH_INTERVAL", "300000"),
                ("FUT_ENABLE_MENU", "1"),
                ("ONLINE/NO_AUTO_SQUAD", "0"),
                # Every session, however deep, ends on the same two calls:
                # userdata, then the tutorial URL, then silence.  CardsDLL
                # pairs RetrieveShouldShowTutorial with a separate
                # RetrieveShouldShowTutorialComplete, so that retrieval is
                # something DoInitialLoginSteps waits on -- and forcing
                # tutorials on is what sends it there.  Turn the step off at
                # its own switches rather than trying to satisfy a parser whose
                # document shape is unknown.
                ("FUT/FORCE_TUTORIALS", "0"),
                ("FUT/DISABLE_TUTORIALS", "1"),
                ("FUT/ALWAYS_SHOW_SMART_TUTORIALS", "0"),
                ("FUT/IS_RETURNING_USER", "0"),
                ("FUT_SKIP_ICEBREAKER_FLOW", "0"),
            ]
        elif name == "OSDK_ROSTER":
            # FIFA's Xbox retail LoadRosterConfig reads these four exact
            # names.  An empty section never publishes the roster-ready event
            # consumed by helperFunctions::checkForFUTRosters.  Version 1.0
            # denotes the shipped/base roster and avoids fabricating a roster
            # download; URL remains local if this build still elects to check.
            values = [
                ("ROSTER_URL", f"{self.identity_base}/roster"),
                ("ROSTER_VER", "1.0"),
                ("ROSTER_LKR", ""),
                ("ROSTER_CSUM", ""),
            ]
        journal_fetch(values)
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

    @staticmethod
    def decode_locale(value: object) -> str:
        """Return the printable four-character locale behind PreAuth's LANG."""
        if not isinstance(value, int) or isinstance(value, bool):
            return ""
        if not 0 < value <= 0xFFFFFFFF:
            return ""
        try:
            text = value.to_bytes(4, "big").decode("ascii")
        except (UnicodeDecodeError, OverflowError):
            return ""
        return text if text.isalpha() else ""

    def preauth(self, request: bytes, state: ClientState | None = None) -> bytes:
        if state is not None:
            language = find_field(decode_frame(request)["fields"], "LANG")
            locale = self.decode_locale(language.value if language else None)
            if locale:
                state.locale = locale
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
        # Authentication2 carries no GTAG, so this login knows no display name
        # of its own.  The FUT auth request does carry the console's real one,
        # so reuse the stored persona instead of overwriting it with the
        # placeholder and advertising a name the client never presented.
        stored_id, stored_name = self.account_store.load_identity()
        if state.gamertag == ClientState.gamertag and stored_id == state.xuid:
            state.gamertag = stored_name
        self.account_store.save_identity(state.xuid, state.gamertag)

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
            return [self.preauth(request, state)]
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
            return [self.fetch_config(request, decoded["fields"], state)]
        if route == (UTIL, UTIL_USER_SETTINGS_LOAD_ALL):
            settings = self.account_store.load_all_settings()
            self.logger.event(
                "user_settings_load_all",
                connection=state.connection_id,
                settings=dict(settings),
            )
            return [
                response_frame(
                    request,
                    encode_fields(
                        [Field("SMAP", MAP, (STRING, STRING, settings))]
                    ),
                )
            ]
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
        account_store: PersistentAccountStore | None = None,
    ):
        self.listen = listen
        self.port = port
        self.advertise = advertise
        self.journal = journal
        self.account_store = account_store or PersistentAccountStore()
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
                content_length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(content_length) if content_length else b""
                owner.journal.event(
                    "identity_http_request",
                    peer=self.client_address[0],
                    method=self.command,
                    path=parsed.path,
                    query_keys=sorted(urllib.parse.parse_qs(parsed.query).keys()),
                    bytes=len(body),
                    headers={name: value for name, value in self.headers.items()},
                    body=request_body_preview(body),
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
                if parsed.path == "/roster":
                    owner.journal.event(
                        "roster_endpoint_requested",
                        peer=self.client_address[0],
                        method=self.command,
                    )
                    # The advertised version identifies the already installed
                    # base roster, so no replacement archive is transferred.
                    self.reply(204)
                    return
                if parsed.path == "/futBoot.xml":
                    owner.journal.event(
                        "fut_boot_served",
                        peer=self.client_address[0],
                        method=self.command,
                        bytes=len(FUT_BOOT_XML),
                    )
                    self.reply(
                        200,
                        FUT_BOOT_XML,
                        {
                            "Content-Type": "application/xml; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                normalized_path = parsed.path
                if normalized_path.startswith("/fut/ut/"):
                    normalized_path = normalized_path[4:]
                # The Xbox CardsDLL names Authentication ``pow/auth`` while
                # the PC client uses ``ut/auth``. Both instantiate the same
                # native response parser and must receive the same SID
                # contract. Other Xbox Cards operations omit the leading
                # ``/ut`` from their path, so normalize those here as well.
                if normalized_path == "/pow/auth":
                    normalized_path = "/ut/auth"
                elif normalized_path.startswith("/game/fifa14/"):
                    normalized_path = "/ut" + normalized_path
                if normalized_path in EASW_AUTH_PATHS:
                    # The native success parser reads these headers and hands
                    # EASW-Session and EASW-Token to CardsDLL.  Supplying them
                    # here is what the retail flow does; writing them straight
                    # into the JSON builder's registers, as an earlier tool
                    # did, satisfied that one constructor while leaving the
                    # EASW session itself unestablished.
                    owner.journal.event(
                        "easw_auth_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                        bytes=len(body),
                        body=request_body_preview(body),
                    )
                    persona_id, _ = owner.account_store.load_identity()
                    self.reply(
                        200,
                        b"",
                        {
                            "Content-Type": "text/plain",
                            "Cache-Control": "no-store",
                            "EASW-Token": EASW_TOKEN,
                            "EASW-Session": EASW_SESSION,
                            "EASW-Nucleus-Persona": str(persona_id),
                            "EASW-Userid": str(persona_id),
                        },
                    )
                    return
                if normalized_path == "/ut/auth":
                    sid = "LOCAL-XBOX360-FIFA14-SID"
                    presented = auth_request_identity(body)
                    if presented is not None:
                        persona_id, persona_name = presented
                        owner.account_store.save_identity(persona_id, persona_name)
                        owner.journal.event(
                            "fut_auth_identity_adopted",
                            peer=self.client_address[0],
                            persona_id=persona_id,
                            persona_name=persona_name,
                        )
                    document = {
                        "sid": sid,
                        "serverTime": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                        ),
                        "lastOnlineTime": "1970-01-01T00:00:00Z",
                    }
                    payload = (
                        json.dumps(document, separators=(",", ":")) + "\n"
                    ).encode("utf-8")
                    owner.journal.event(
                        "fut_ut_auth_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                        bytes=len(body),
                        content_type=self.headers.get("Content-Type"),
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                            "X-UT-SID": sid,
                        },
                    )
                    return
                if (
                    normalized_path == "/ut/game/fifa14/user"
                    and self.command == "GET"
                ):
                    # FutGetUserInfoServerResponse zeroes every account field
                    # and treats all members as optional -- which is why an
                    # empty object here showed a zero balance in the club
                    # header.  The currency belongs in this response, not in
                    # user/credits.  Matching stays method-specific: a later
                    # create-user POST to the same path must remain unhandled
                    # until it is observed.
                    self.reply(
                        200,
                        WALLET.user_info(CLUB_NAME, "FUT") + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    owner.journal.event(
                        "fut_user_info_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    return
                if normalized_path == "/ut/game/fifa14/match/reset":
                    self.reply(
                        200,
                        b'{"reset":true}\n',
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    owner.journal.event(
                        "fut_match_reset_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    return
                # Anything that describes the club is generated from the
                # inventory rather than answered with an empty fixture: an
                # empty squad is what fcc_login2 treats as fatal, and an empty
                # club leaves nothing to field a match with.
                club_responses = {
                    "/ut/game/fifa14/squad/list": (
                        lambda: CLUB_INVENTORY.squad_list_response(CLUB_NAME)
                    ),
                    "/ut/game/fifa14/squad/active": (
                        lambda: CLUB_INVENTORY.active_squad_response(CLUB_NAME)
                    ),
                    "/ut/game/fifa14/club": CLUB_INVENTORY.club_response,
                    "/ut/game/fifa14/purchased/items": (
                        CLUB_INVENTORY.purchased_items_response
                    ),
                }
                # A quick sell is what actually writes the header's balance, so
                # its reply has to carry the new total. An empty object here is
                # what left the header printing uninitialised memory.
                # Polled straight after every search, and it refreshes the
                # header too, so it carries the balance as well.
                if normalized_path in (
                    "/ut/game/fifa14/tradePile",
                    "/ut/game/fifa14/tradepile",
                ) and self.command == "GET":
                    self.reply(
                        200,
                        CARD_ACTIONS.trade_pile(WALLET.coins) + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # The client asks about one auction by id before bidding on
                # it; an empty answer reads as "Auction state is invalid for
                # bidding", which is the string CardsDLL carries beside
                # /status?tradeIds=%lld.
                if (
                    normalized_path == "/ut/game/fifa14/trade/status"
                    and self.command == "GET"
                    and parsed.query
                ):
                    asked: list[int] = []
                    for raw in urllib.parse.parse_qs(parsed.query).get("tradeIds", []):
                        for piece in raw.split(","):
                            try:
                                asked.append(int(piece))
                            except ValueError:
                                continue
                    payload = CARD_CATALOGUE.status_for(asked, WALLET.coins)
                    owner.journal.event(
                        "fut_trade_status",
                        peer=self.client_address[0],
                        asked=len(asked),
                        known=len(CARD_CATALOGUE.served),
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path in (
                    "/ut/game/fifa14/trade/status",
                    "/ut/game/fifa14/watchlist",
                ) and self.command == "GET":
                    self.reply(
                        200,
                        WALLET.auction_state() + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Buying the one pack the store advertises. A POST here is the
                # purchase; the drawn cards come back in the reply and then
                # again from purchased/items until the client takes them.
                # Buying a pack is a POST to purchased/items, not to /store --
                # the journal shows the client sending it there and getting a
                # 404. /store is only the catalogue.
                # Seasons, cups and Team of the Week. Each of these screens
                # treats an empty list as an error rather than as "nothing
                # available" -- the same way fcc_login2 treats an empty squad --
                # so serving a real one is what makes the mode selectable.
                mode_responses = {
                    "/ut/game/fifa14/season/list": seasons_response,
                    "/ut/game/fifa14/season/user": season_user_response,
                    "/ut/game/fifa14/tournament/list": tournaments_response,
                    "/ut/game/fifa14/tournament/user/list": (
                        active_tournaments_response
                    ),
                    "/ut/game/fifa14/user/list": (
                        lambda: totw_index_with_squad(CARD_CATALOGUE)
                    ),
                    "/ut/game/fifa14/clientdata/totw": (
                        lambda: totw_response(CARD_CATALOGUE)
                    ),
                }
                if normalized_path in mode_responses and self.command == "GET":
                    payload = mode_responses[normalized_path]()
                    owner.journal.event(
                        "fut_mode_request",
                        peer=self.client_address[0],
                        path=parsed.path,
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # The whole catalogue, generated: nine packs with their own
                # prices and groups, rather than the single fixture entry.
                if normalized_path in (
                    "/ut/game/fifa14/store/purchasegroup/all",
                    "/ut/game/fifa14/store",
                ) and self.command == "GET":
                    self.reply(
                        200,
                        store_catalogue() + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if (
                    normalized_path
                    in ("/ut/game/fifa14/purchased/items", "/ut/game/fifa14/store")
                    and self.command == "POST"
                ):
                    # The body names which pack, so the 400-coin bronze costs
                    # 400 rather than whatever the default is.
                    try:
                        wanted = json.loads(body or b"{}")
                    except ValueError:
                        wanted = {}
                    try:
                        pack_id = int(
                            wanted.get("packId") or wanted.get("id") or GOLD_PACK_ID
                        )
                    except (TypeError, ValueError):
                        pack_id = GOLD_PACK_ID
                    if not PACK_SHOP.can_afford(pack_id):
                        owner.journal.event(
                            "fut_pack_refused",
                            peer=self.client_address[0],
                            coins=WALLET.coins,
                        )
                        self.reply(
                            409,
                            PACK_SHOP.refused() + b"\n",
                            {"Content-Type": "application/json; charset=utf-8"},
                        )
                        return
                    payload = PACK_SHOP.open_pack(pack_id)
                    CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS)
                    owner.journal.event(
                        "fut_pack_opened",
                        peer=self.client_address[0],
                        coins=WALLET.coins,
                        pack=pack_id,
                        items=len(PACK_SHOP.pending),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if (
                    normalized_path == "/ut/game/fifa14/purchased/items"
                    and self.command == "GET"
                ):
                    self.reply(
                        200,
                        PACK_SHOP.purchased_items() + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Anything else under /trade/. CardsDLL composes these from
                # fragments -- "?tradeId=", "/expired", "/status?tradeIds=" --
                # and an unanswered one reads as the listing being gone, which
                # is what "la liste a expiré" says. Answer the ones we know and
                # give the rest an empty, well-formed auction list rather than
                # a 404.
                if normalized_path.startswith(
                    "/ut/game/fifa14/trade"
                ) and normalized_path.endswith("/expired") and self.command == "GET":
                    self.reply(
                        200,
                        b'{"auctionInfo":[],"duplicateItemIdList":[],"total":0}\n',
                        {"Content-Type": "application/json; charset=utf-8"},
                    )
                    return
                # Bidding and buying. Both go through the same endpoint: a bid
                # at or above the buy-now price ends the auction, which is what
                # the Buy Now button does.
                # Buying posts to /offer, not /bid. That 404 is what the
                # screen reports as "cette liste a expiré" -- the timer was
                # showing 23h59 at the time, so the message names the wrong
                # cause and only the journal says which request was missed.
                if (
                    normalized_path.startswith("/ut/game/fifa14/trade/")
                    and normalized_path.rsplit("/", 1)[-1] in ("bid", "offer")
                    and self.command in ("PUT", "POST")
                ):
                    parts = normalized_path.split("/")
                    try:
                        trade_id = int(parts[-2])
                    except (IndexError, ValueError):
                        trade_id = 0
                    try:
                        document = json.loads(body or b"{}")
                    except ValueError:
                        document = {}
                    try:
                        amount = int(
                            document.get("bid")
                            or document.get("buyNowPrice")
                            or document.get("amount")
                            or 0
                        )
                    except (TypeError, ValueError):
                        amount = 0
                    payload, won = CARD_CATALOGUE.bid(trade_id, amount, WALLET)
                    if won is not None:
                        # A bought card goes to the pending pile, not straight
                        # into the club. That is the route the pack flow takes
                        # and the one that works: purchased/items is what the
                        # assign screen reads, and sending the card directly to
                        # the club left that list empty -- so "Assigner
                        # maintenant" had nothing to offer and backed out.
                        item = dict(won)
                        item["itemState"] = "new"
                        item["untradeable"] = False
                        PACK_SHOP.pending.append(item)
                        CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS)
                    CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS)
                    owner.journal.event(
                        "fut_bid",
                        peer=self.client_address[0],
                        trade=trade_id,
                        amount=amount,
                        won=won is not None,
                        coins=WALLET.coins,
                        body=request_body_preview(body),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Putting a card on the market, and taking it back off.
                if normalized_path in (
                    "/ut/game/fifa14/auctionhouse",
                    "/ut/game/fifa14/trade",
                ) and self.command == "POST":
                    try:
                        document = json.loads(body or b"{}")
                    except ValueError:
                        document = {}
                    payload = CARD_ACTIONS.list_for_sale(document)
                    CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS)
                    owner.journal.event(
                        "fut_item_listed",
                        peer=self.client_address[0],
                        path=parsed.path,
                        listings=len(CARD_ACTIONS.listings),
                        # Kept so an unexpected body shape can be read back out
                        # of the journal rather than guessed at again.
                        body=request_body_preview(body),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path.startswith(
                    "/ut/game/fifa14/auctionhouse/"
                ) and self.command == "DELETE":
                    tail = normalized_path.rsplit("/", 1)[-1]
                    try:
                        trade_id = int(tail)
                    except ValueError:
                        trade_id = 0
                    self.reply(
                        200,
                        CARD_ACTIONS.withdraw(trade_id) + b"\n",
                        {"Content-Type": "application/json; charset=utf-8"},
                    )
                    return
                # Saving a squad. The body names the cards and their slots;
                # without this the squad was whatever was built at load time
                # and nothing could ever change it, so a card bought or pulled
                # reached the club and had nowhere to go.
                if normalized_path.startswith(
                    "/ut/game/fifa14/squad/"
                ) and self.command in ("PUT", "POST"):
                    try:
                        document = json.loads(body or b"{}")
                    except ValueError:
                        document = {}
                    squad = document.get("squad", document)
                    chosen: list[int] = []
                    for entry in (squad.get("players") or []):
                        data = entry.get("itemData") if isinstance(entry, dict) else None
                        raw = (data or {}).get("id") if isinstance(data, dict) else None
                        try:
                            if raw:
                                chosen.append(int(raw))
                        except (TypeError, ValueError):
                            continue
                    if chosen:
                        CLUB_INVENTORY.set_squad(chosen)
                        CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS)
                    owner.journal.event(
                        "fut_squad_saved",
                        peer=self.client_address[0],
                        path=parsed.path,
                        players=len(chosen),
                        body=request_body_preview(body),
                    )
                    self.reply(
                        200,
                        b'{"id":1}\n',
                        {"Content-Type": "application/json; charset=utf-8"},
                    )
                    return
                # Send to club, list for transfer: each entry has to be
                # acknowledged. Answering with a club search acknowledges
                # nothing and the button looks dead.
                if normalized_path == "/ut/game/fifa14/item" and self.command in (
                    "PUT",
                    "POST",
                ):
                    try:
                        document = json.loads(body or b"{}")
                    except ValueError:
                        document = {}
                    payload = CARD_ACTIONS.move(document)
                    CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS)
                    owner.journal.event(
                        "fut_item_move",
                        peer=self.client_address[0],
                        path=parsed.path,
                        club=len(CARD_ACTIONS.club),
                        pending=len(PACK_SHOP.pending),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path == "/ut/delete/game/fifa14/item":
                    # {"itemId":[...]} -- always a list, twelve long when a
                    # whole pack is sold at once.
                    item_ids: list[int] = []
                    try:
                        document = json.loads(body or b"{}")
                    except ValueError:
                        document = {}
                    raw = document.get("itemId", document.get("id"))
                    if isinstance(raw, list):
                        candidates = raw
                    elif raw is not None:
                        candidates = [raw]
                    else:
                        candidates = urllib.parse.parse_qs(parsed.query).get("id", [])
                    for candidate in candidates:
                        try:
                            item_ids.append(int(candidate))
                        except (TypeError, ValueError):
                            continue
                    payload = CARD_ACTIONS.discard_many(item_ids)
                    CLUB_SAVE.save(CLUB_INVENTORY, WALLET, CARD_ACTIONS)
                    owner.journal.event(
                        "fut_quick_sell",
                        peer=self.client_address[0],
                        path=parsed.path,
                        coins=WALLET.coins,
                        items=len(item_ids),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path in (
                    "/ut/game/fifa14/user/credits",
                    "/ut/game/fifa14/user",
                ) and self.command == "GET":
                    payload = (
                        WALLET.credits_response()
                        if normalized_path.endswith("/credits")
                        else WALLET.user_info(CLUB_NAME, "FUT")
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # The market is the one screen whose job is to show players the
                # club does not own, so it is served from the catalogue rather
                # than the inventory.
                if normalized_path in (
                    "/ut/game/fifa14/transfermarket",
                    "/ut/game/fifa14/club",
                ) and self.command == "GET" and parsed.query:
                    query = {
                        key: values[0]
                        for key, values in urllib.parse.parse_qs(parsed.query).items()
                    }
                    if normalized_path.endswith("/club"):
                        # A club search still searches the club -- but it does
                        # search it now, rather than returning all of it.
                        payload = CLUB_INVENTORY.club_response(query)
                    else:
                        payload = CARD_CATALOGUE.auctions(query, coins=WALLET.coins)
                    owner.journal.event(
                        "fut_market_search",
                        peer=self.client_address[0],
                        path=parsed.path,
                        filters=sorted(query),
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path in club_responses and self.command == "GET":
                    payload = club_responses[normalized_path]()
                    owner.journal.event(
                        "fut_club_response",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # The four the FUT home fetches for itself. The header reads
                # its balance from whichever response last carried one, and
                # these are the last ones before it draws -- which is why it
                # showed zero at home while the store, which refetches credits,
                # showed the real figure.
                #
                # Only these three, and the list was found by bisection rather
                # than reasoning. Adding the balance to every FUT route froze
                # the login at clientdata/tutorialpopups; adding it to
                # clientdata/userHubData as well froze it there instead. Both
                # of those parsers reject an object carrying members they do
                # not know, and the login step waiting on the response never
                # completes. Do not extend this list without watching where the
                # fan-out stops.
                # The Consommables tab asks here by category, and it was a
                # 404 -- so the tab looked empty however many the club held.
                if normalized_path.startswith(
                    "/ut/game/fifa14/club/consumables"
                ) and self.command == "GET":
                    self.reply(
                        200,
                        consumables_response(CLUB_INVENTORY) + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # Item definitions the trophy and club tiles resolve against.
                if normalized_path.startswith("/fut/items/") and self.command == "GET":
                    self.reply(
                        200,
                        b'{"itemData":[]}\n',
                        {"Content-Type": "application/json; charset=utf-8"},
                    )
                    return
                # Mon Club's counters -- players, rares, staff, stadiums,
                # kits, badges, balls -- all read zero because these answered
                # with an empty entries list, so a club full of cards reported
                # owning nothing.
                if normalized_path.startswith(
                    "/ut/game/fifa14/club/stats/"
                ) and self.command == "GET":
                    self.reply(
                        200,
                        club_stats_response(CLUB_INVENTORY) + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                # The My Club and transfer tiles read their counts here, and
                # both were fixed: the club tile stayed at 92 as cards arrived
                # and the market tile always read zero.
                if normalized_path == "/ut/game/fifa14/hub" and self.command == "GET":
                    self.reply(
                        200,
                        with_balance(
                            hub_response(
                                CLUB_INVENTORY, len(CARD_ACTIONS.listings)
                            ),
                            WALLET.coins,
                        )
                        + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path in (
                    "/ut/game/fifa14/hub",
                    "/ut/game/fifa14/eventfeed",
                    "/ut/game/fifa14/clubUser",
                ) and self.command == "GET":
                    self.reply(
                        200,
                        with_balance(FUT_ROUTES[normalized_path], WALLET.coins)
                        + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path in FUT_ROUTES:
                    owner.journal.event(
                        "fut_route_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    self.reply(
                        200,
                        FUT_ROUTES[normalized_path] + b"\n",
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path == "/ut/game/fifa14/settings":
                    # Field names recovered from FIFA 14's FutSettings parser.
                    # clubCreateThreshold stays at zero so a brand-new account
                    # is allowed to create its club immediately.
                    payload = (
                        json.dumps(
                            {
                                "maximumTradePileSize": 30,
                                "getOperationTimeoutSec": 60,
                                "clubCreateThreshold": 0,
                                "fifaPointsCancelTransactionFix": 1,
                                "tokenRedemptionEnabled": 0,
                                "enableWorldCupMode": 0,
                            },
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8")
                    owner.journal.event(
                        "fut_settings_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if parsed.path.startswith(("/fut/loc/", "/fut/packs/loc/")):
                    # Localisation bundles for the FUT leaderboard and pack
                    # screens.  The client only needs a well-formed document;
                    # an empty string table keeps the retail labels in place.
                    owner.journal.event(
                        "fut_locstring_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    self.reply(
                        200,
                        b'<?xml version="1.0" encoding="utf-8"?>\n'
                        b"<localization>\n</localization>\n",
                        {
                            "Content-Type": "application/xml; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if normalized_path == "/ut/game/fifa14/user/accountinfo":
                    persona_id, persona_name = owner.account_store.load_identity()
                    # An empty persona list is what the PC revival serves, and
                    # the difference is not cosmetic.  A populated list tells
                    # the client it already owns a FUT account, so the login
                    # helper goes looking for that account's club, squad and
                    # identity -- none of which exist here -- and waits on a
                    # completion that never arrives.  An empty list states the
                    # opposite: no FUT account yet.  That is the NEW_USER path
                    # fcc_login1 already knows how to walk, through the
                    # icebreaker captain selection into club creation.
                    document = {
                        "userAccountInfo": {
                            "personas": [],
                            "returningUser": False,
                        }
                    }
                    payload = (
                        json.dumps(document, separators=(",", ":")) + "\n"
                    ).encode("utf-8")
                    owner.journal.event(
                        "fut_account_info_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                        persona_id=persona_id,
                        persona_name=persona_name,
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                lowered_path = normalized_path.lower()
                if lowered_path == "/ut/game/fifa14/phishing/trusteddevice":
                    # An empty object leaves the device unknown, so the client
                    # asks its security question on every single launch -- and
                    # answering it is what makes this server persist the flags
                    # that then stop the client authenticating at all.  The
                    # question is not a step of a working FUT login; it is a
                    # detour an unrecognised device is sent on.
                    #
                    # CardsDLL's parser reads exactly four booleans here.
                    # Describing the console as a device we already know, whose
                    # fingerprint has not changed and which is not locked, is
                    # what the working reference implementation of this title
                    # serves. It grants no account, club, inventory or
                    # entitlement -- only that this device has been seen before.
                    payload = (
                        b'{"trusted":true,"changed":false,'
                        b'"exists":true,"locked":false}\n'
                    )
                    owner.journal.event(
                        "fut_trusted_device_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if lowered_path in (
                    "/ut/game/fifa14/phishing",
                    "/ut/game/fifa14/phishing/question",
                ):
                    document = {
                        "question": 0,
                        "attempts": 5,
                        "recoverAttempts": 20,
                    }
                    payload = (
                        json.dumps(document, separators=(",", ":")) + "\n"
                    ).encode("utf-8")
                    owner.journal.event(
                        "fut_phishing_question_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if lowered_path == "/ut/game/fifa14/phishing/validate":
                    document = {
                        "debug": "Answer is correct.",
                        "string": "OK",
                        "code": "200",
                        "reason": "Answer is correct.",
                        "token": "LOCAL-FIFA14-PHISHING",
                    }
                    payload = (
                        json.dumps(document, separators=(",", ":")) + "\n"
                    ).encode("utf-8")
                    owner.journal.event(
                        "fut_phishing_validation_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                            "Set-Cookie": (
                                "FUTWebPhishing=LOCAL-FIFA14-PHISHING; "
                                "Path=/; HttpOnly"
                            ),
                        },
                    )
                    return
                if lowered_path == "/ut/game/fifa14/user/action":
                    # GetUserActionServerResponse is a collection. A new local
                    # identity has no completed onboarding actions yet.
                    payload = b'{"userActionList":[]}\n'
                    owner.journal.event(
                        "fut_user_actions_request",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if lowered_path.startswith("/ut/game/fifa14/user/action/"):
                    # UpdateUserActionServerResponse has no parsed payload.
                    payload = b"{}\n"
                    owner.journal.event(
                        "fut_user_action_update",
                        peer=self.client_address[0],
                        method=self.command,
                        effective_method=self.headers.get(
                            "X-HTTP-Method-Override", self.command
                        ).upper(),
                        path=parsed.path,
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if lowered_path.endswith(
                    "/fut/packs/icebreaker/icebreakerpacklist.json"
                ) or lowered_path.endswith(
                    "/packs/icebreaker/icebreakerpacklist.json"
                ):
                    # id and image alone are enough to draw the four dock
                    # rows, but not to build the cards behind them: the retail
                    # CardsDLL card constructor dereferences a null player
                    # object when the squad resource ids are absent, and the
                    # client restarts its whole bootstrap.  Serve a fixture
                    # that carries the 23-player arrays each pack declares.
                    payload = (
                        ICEBREAKER_PACK_LIST.read_text(encoding="utf-8").strip()
                        + "\n"
                    ).encode("utf-8")
                    owner.journal.event(
                        "fut_icebreaker_packlist_served",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload,
                        {
                            "Content-Type": "application/json; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
                    return
                if (
                    lowered_path.endswith("/loc/xbox360/leaderboards.eng_us.xml")
                    or lowered_path.endswith("/loc/xbox360/icebreaker.eng_us.xml")
                ):
                    payload = (
                        b'<?xml version="1.0" encoding="UTF-8"?>\n'
                        b'<message_set target="fut-locstrings">\n'
                        b'  <locstring id="FUT_IB_CAPTAINNAME_0">FALCAO</locstring>\n'
                        b'  <locstring id="FUT_IB_CAPTAINNAME_1">MESSI</locstring>\n'
                        b'  <locstring id="FUT_IB_CAPTAINNAME_2">EL SHAARAWY</locstring>\n'
                        b'  <locstring id="FUT_IB_CAPTAINNAME_3">ALABA</locstring>\n'
                        b'</message_set>\n'
                    )
                    owner.journal.event(
                        "fut_locstrings_served",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                        bytes=len(payload),
                    )
                    self.reply(
                        200,
                        payload,
                        {"Content-Type": "application/xml; charset=utf-8"},
                    )
                    return
                if normalized_path in ("/messages", "/fut/messages"):
                    self.reply(
                        200,
                        b'<?xml version="1.0" encoding="UTF-8"?>\n<MESSAGES>\n</MESSAGES>\n',
                        {"Content-Type": "application/xml; charset=utf-8"},
                    )
                    return
                if normalized_path in ("/tutorials", "/fut/tutorials"):
                    # Every recorded session ends on this request, whatever
                    # else changes, and disabling FUT/DISABLE_TUTORIALS and
                    # FUT/FORCE_TUTORIALS did not stop the client making it --
                    # so it is not gated by those keys and the only thing left
                    # to vary is the answer.  An empty <MESSAGES> document was
                    # a guess whose shape was never checked against the
                    # parser; 404 is the one answer whose meaning is
                    # unambiguous.  If the client can treat "no tutorials" as
                    # ordinary, this is what tells it so.
                    owner.journal.event(
                        "fut_tutorial_feed_declined",
                        peer=self.client_address[0],
                        method=self.command,
                        path=parsed.path,
                    )
                    self.reply(404, b"not found\n", {"Content-Type": "text/plain"})
                    return
                if parsed.path == "/sponsored-events":
                    # The title only needs a valid non-empty URL during the
                    # global online bootstrap.  Keep the local target benign
                    # in case a menu later opens it in the embedded browser.
                    self.reply(204)
                    return
                # An unhandled route is the clearest signal that the retail
                # client expects a document this server does not model yet.
                owner.journal.event(
                    "identity_http_unhandled",
                    peer=self.client_address[0],
                    method=self.command,
                    path=parsed.path,
                    normalized_path=normalized_path,
                    query_keys=sorted(urllib.parse.parse_qs(parsed.query).keys()),
                    body=request_body_preview(body),
                )
                self.reply(404, b"not found\n", {"Content-Type": "text/plain"})

            do_GET = serve_identity
            do_HEAD = serve_identity
            do_POST = serve_identity
            do_PUT = serve_identity
            do_DELETE = serve_identity

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
        tls_context: ssl.SSLContext | None = None,
        tls_ports: set[int] | None = None,
    ):
        self.listen = listen
        self.ports = ports
        self.protocol = protocol
        self.journal = journal
        self.tls_context = tls_context
        self.tls_ports = tls_ports or set()
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
            self.journal.event(
                "listening",
                address=self.listen,
                port=port,
                transport="tls" if port in self.tls_ports else "plaintext",
            )

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
        if state.local_port in self.tls_ports:
            if self.tls_context is None:
                self.journal.event(
                    "tls_configuration_error",
                    connection=state.connection_id,
                    local_port=state.local_port,
                )
                client.close()
                return
            try:
                client.settimeout(8.0)
                client = self.tls_context.wrap_socket(client, server_side=True)
                cipher = client.cipher()
                self.journal.event(
                    "tls_connected",
                    connection=state.connection_id,
                    local_port=state.local_port,
                    version=client.version(),
                    cipher=cipher[0] if cipher else None,
                )
            except (OSError, ssl.SSLError) as error:
                self.journal.event(
                    "tls_handshake_error",
                    connection=state.connection_id,
                    local_port=state.local_port,
                    error=f"{type(error).__name__}: {error}",
                )
                try:
                    client.close()
                except OSError:
                    pass
                return

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


def build_redirector_tls_context(cert: Path, key: Path) -> ssl.SSLContext:
    """Build the TLS 1.0 context expected by the retail ProtoSSL client."""

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1
    context.maximum_version = ssl.TLSVersion.TLSv1
    # FIFA 14's ProtoSSL predates modern AEAD suites.  SECLEVEL=0 is required
    # for its TLS 1.0/RSA handshake and the deliberately 1024-bit test key.
    context.set_ciphers("AES128-SHA:AES256-SHA:@SECLEVEL=0")
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    return context


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
    parser.add_argument(
        "--redirector-tls-ports",
        type=parse_ports,
        default=parse_ports("42127"),
        help=(
            "comma-separated Blaze redirector ports to wrap in native TLS "
            "(default: 42127)"
        ),
    )
    parser.add_argument(
        "--redirector-tls-cert",
        type=Path,
        help="PEM certificate using the old ProtoSSL signature-OID workaround",
    )
    parser.add_argument(
        "--redirector-tls-key",
        type=Path,
        help="PEM private key for --redirector-tls-cert",
    )
    args = parser.parse_args()

    if (args.redirector_tls_cert is None) != (args.redirector_tls_key is None):
        parser.error(
            "--redirector-tls-cert and --redirector-tls-key must be used together"
        )
    tls_context = None
    tls_ports: set[int] = set()
    if args.redirector_tls_cert is not None:
        missing_tls_ports = set(args.redirector_tls_ports).difference(args.ports)
        if missing_tls_ports:
            parser.error(
                "every --redirector-tls-ports value must also be present in "
                f"--ports (missing: {sorted(missing_tls_ports)})"
            )
        tls_context = build_redirector_tls_context(
            args.redirector_tls_cert,
            args.redirector_tls_key,
        )
        tls_ports.update(args.redirector_tls_ports)

    journal = Journal(args.journal)
    account_store = PersistentAccountStore(args.account_state)
    protocol = Fifa14Protocol(
        args.advertise,
        args.core_port,
        journal,
        identity_port=args.identity_port,
        account_store=account_store,
    )
    service = BlazeService(
        args.listen,
        args.ports,
        protocol,
        journal,
        tls_context=tls_context,
        tls_ports=tls_ports,
    )
    identity = IdentityHttpService(
        args.listen,
        args.identity_port,
        args.advertise,
        journal,
        account_store,
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
        redirector_transport=("tls" if tls_ports else "plaintext"),
        redirector_tls_ports=(sorted(tls_ports) if tls_ports else []),
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
