"""Build a FUT club inventory out of cards this build already ships.

A FUT match needs eleven real player items, and the screens before it need a
club to look at: an inventory, a squad, a kit, a badge, a stadium, a ball. None
of that can be invented from nothing -- an asset id with no art behind it draws
a blank card, and the squad screen rejects records it cannot resolve.

The card database itself (`data/db/cards_ng_db.db` inside `cards0.big`) does
not decode yet: its chunks report an LZX block type of 7 at the first symbol,
which no window size fixes. But the icebreaker pack list does decode, and it is
the same build's own data -- four starter squads of twenty-three players, each
with a real asset id, rating, rare flag, club and six attributes. 158023 is
Messi, 167397 is Neymar. Those are the cards this disc can actually draw.

So the catalogue here is not synthetic. It is the icebreaker squads, unpacked
into the item shape the FUT endpoints expect.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path


PACK_LIST = Path(__file__).resolve().parent / "icebreakerpacklist.json"

# resourceId carries the asset id in its low 24 bits and a version in the high
# byte; version 1 is the base card. The PC revival asserts the same invariant.
RESOURCE_VERSION = 0x0100_0000

# Item ids have to be unique and stable across a session, and must not collide
# with the presentation items below.
FIRST_PLAYER_ITEM_ID = 1_600_000_001

FORMATION = "f442"

# Positions come from the card catalogue, keyed by asset id.
#
# Two earlier attempts were wrong and both are worth remembering. Assigning a
# position per squad slot put Messi at right back, because the pack squads are
# not in formation order -- slot 10 of the first pack is Neuer. Sending no
# position at all, on the theory that the title would resolve it from the same
# record it resolves the portrait and club badge from, made every player a
# goalkeeper: it does not fall back, it defaults.
#
# So the position has to be supplied, and it has to be right. The catalogue in
# `fifa14_cards.json` carries one per card, and its asset ids are the game's --
# Messi is 158023 with club 241 there, exactly as in the icebreaker fixture.
CARD_CATALOGUE = Path(__file__).resolve().parent / "fifa14_cards.json"

# Used only when a card is missing from the catalogue. Outfield rather than GK,
# because a wrong outfield position costs chemistry while a wrong goalkeeper
# costs the match.
FALLBACK_POSITION = "CM"

# The eleven slots of f442, in the order the squad screen walks them, each with
# the positions that can fill it from best fit to worst. The pack squads are
# not in this order -- the first pack has its goalkeeper at slot 10 -- so the
# starting eleven has to be arranged rather than taken as it comes.
F442_SLOTS = [
    ("GK", ("GK",)),
    ("LB", ("LB", "LWB", "CB")),
    ("CB", ("CB", "RB", "LB")),
    ("CB", ("CB", "CDM", "RB")),
    ("RB", ("RB", "RWB", "CB")),
    ("LM", ("LM", "LW", "LWB", "CM")),
    ("CM", ("CM", "CDM", "CAM")),
    ("CM", ("CM", "CDM", "CAM")),
    ("RM", ("RM", "RW", "RWB", "CM")),
    ("ST", ("ST", "CF", "LW", "RW")),
    ("ST", ("ST", "CF", "LW", "RW")),
]


def _arrange(players: list[dict]) -> list[dict]:
    """Put the best fit in each slot, then bench whoever is left."""
    remaining = list(players)
    eleven: list[dict] = []
    for _, preferred in F442_SLOTS:
        pick = None
        for position in preferred:
            candidates = [p for p in remaining if p["preferredPosition"] == position]
            if candidates:
                pick = max(candidates, key=lambda p: p["rating"])
                break
        if pick is None:
            # Nobody plays there; take the highest rated rather than leave the
            # slot empty, which the squad screen treats as an incomplete side.
            pick = max(remaining, key=lambda p: p["rating"])
        remaining.remove(pick)
        eleven.append(pick)
    return eleven + sorted(remaining, key=lambda p: -p["rating"])


# Leagues the Xbox build does not appear to carry. A card from one of these
# draws with "Club undefined" and the raw localisation key
# "*leagueName_abbr15_0" instead of a league name -- the title is failing to
# resolve the id, not mis-reading the card.
#
# Which leagues those are cannot be determined from here: the catalogue holds
# 42 and the console knows some subset. This list is the suspect end of that
# range -- lower divisions and regional competitions -- and is meant to be
# corrected from what the screen actually shows rather than trusted as it is.
UNRESOLVED_LEAGUES = {
    2002,   # Nacional B
    2025,   # Liga do Brasil B
    332,    # Ukrayina Liha
    336,    # Liga Postobón
    347,    # South African FL
    371,    # Scotland League
}


def _card_resolves(card: dict) -> bool:
    """Keep only cards the title can actually draw.

    A missing club or league leaves the card showing placeholder text where its
    badge and competition should be, which is worse than the card not being
    offered at all.
    """
    if not (card.get("name") or "").strip():
        return False
    if not card.get("clubId") or not card.get("nationId"):
        return False
    if card.get("leagueId") in UNRESOLVED_LEAGUES:
        return False
    if (card.get("club") or "").strip().lower() in ("", "undefined"):
        return False
    return True


def _cards_by_asset() -> dict[int, dict]:
    """The catalogue keyed by asset id.

    The icebreaker packs carry an asset id, a rating, a rare flag, a club and
    six attributes -- but no position, nation or league. Leaving those at zero
    is why a club search for, say, a Cameroonian centre back returned the whole
    squad: every card matched every nation, because every card had nation 0.
    """
    if not CARD_CATALOGUE.exists():
        return {}
    document = json.loads(CARD_CATALOGUE.read_text())
    by_asset: dict[int, dict] = {}
    for card in document.get("cards", []):
        if _card_resolves(card):
            by_asset.setdefault(int(card["assetId"]), card)
    return by_asset

# Kit, badge, stadium and ball. Without these the club has nothing to present
# and the match cannot dress either side. Asset ids are the retail defaults the
# PC revival also uses.
PRESENTATION_ACTIVES = [
    {"id": 1700000001, "assetId": 14, "resourceId": 6300000, "rating": 0,
     "itemType": "kit", "itemState": "activeHomeKit"},
    {"id": 1700000002, "assetId": 15, "resourceId": 6400001, "rating": 0,
     "itemType": "kit", "itemState": "activeAwayKit"},
    {"id": 1700000003, "assetId": 241, "resourceId": 6000000, "rating": 0,
     "itemType": "badge", "itemState": "activeBadge"},
    {"id": 1700000004, "assetId": 6, "resourceId": 6200004, "rating": 0,
     "itemType": "stadium", "itemState": "activeStadium"},
    {"id": 1700000005, "assetId": 23, "resourceId": 8120091, "rating": 0,
     "itemType": "ball", "itemState": "activeBall"},
]


def _presentation_items() -> list[dict]:
    items = []
    for base in PRESENTATION_ACTIVES:
        item = dict(base)
        item.update(
            {
                "discardValue": 0,
                "lastSalePrice": 0,
                "timestamp": 1,
                "untradeable": True,
            }
        )
        items.append(item)
    return items


def _player_item(
    item_id: int,
    asset_id: int,
    rating: int,
    rare: int,
    play_style: int,
    team_id: int,
    attributes: list[int],
    position: str,
    item_state: str = "free",
    nation: int = 0,
    league: int = 0,
    rarity: str = "",
) -> dict:
    return {
        "id": item_id,
        "assetId": asset_id,
        "resourceId": RESOURCE_VERSION | asset_id,
        "rating": rating,
        "preferredPosition": position,
        "teamid": team_id,
        "leagueId": league,
        "nation": nation,
        # Kept for our own filtering; the client ignores members it does not
        # know at the top level of an item.
        "rarity": rarity,
        "itemType": "player",
        "itemState": item_state,
        "formation": FORMATION,
        # A full contract and full fitness: a card that cannot take the field
        # is the same as no card at all for a first match.
        "contract": 99,
        "fitness": 99,
        "injuryGames": 0,
        "injuryType": "none",
        "suspension": 0,
        "training": 0,
        "playStyle": play_style,
        # A quick sell pays this. Zero everywhere meant selling a card returned
        # nothing, which is also how the balance first showed up wrong.
        "discardValue": max(10, (rating - 40) ** 2 // 20) if rating else 0,
        "lastSalePrice": 0,
        "timestamp": 1,
        "untradeable": True,
        "rareflag": rare,
        "cardsubtypeid": 1 if rare else 0,
        "assists": 0,
        "lifetimeAssists": 0,
        "attributeList": [
            {"index": index, "value": value} for index, value in enumerate(attributes)
        ],
        "statsList": [{"index": index, "value": 0} for index in range(5)],
        "lifetimeStats": [{"index": index, "value": 0} for index in range(5)],
    }


class ClubInventory:
    """Every card the club owns, plus the squad that starts."""

    def __init__(self, pack_list: Path = PACK_LIST) -> None:
        document = json.loads(pack_list.read_text())
        packs = document["packList"]
        catalogue = _cards_by_asset()

        self.items: list[dict] = []
        self.squad: list[dict] = []
        next_id = FIRST_PLAYER_ITEM_ID

        for pack_index, pack in enumerate(packs):
            attributes = [pack[f"Attribute{n}"] for n in range(1, 7)]
            for slot, asset_id in enumerate(pack["squad"]):
                item = _player_item(
                    item_id=next_id,
                    asset_id=asset_id,
                    rating=pack["Rating"][slot],
                    rare=pack["Rare"][slot],
                    play_style=pack["playStyle"][slot],
                    team_id=pack["teamId"][slot],
                    attributes=[column[slot] for column in attributes],
                    position=(catalogue.get(asset_id) or {}).get("position")
                    or FALLBACK_POSITION,
                    nation=(catalogue.get(asset_id) or {}).get("nationId", 0),
                    league=(catalogue.get(asset_id) or {}).get("leagueId", 0),
                    rarity=(catalogue.get(asset_id) or {}).get("rarity", ""),
                )
                self.items.append(item)
                # The first pack becomes the starting squad; the rest stay in
                # the club as spares, which is what gives the transfer and club
                # screens something to show.
                if pack_index == 0:
                    self.squad.append(item)
                next_id += 1

        self.squad = _arrange(self.squad)
        self.items.extend(_presentation_items())
        # Consumables, kits, badges, stadiums, balls and staff. Without them
        # the Consommables, Elements club and Personnel tabs are empty and
        # their filters have nothing to act on.
        self.items.extend(_club_extras())

    # -- responses -------------------------------------------------------

    def club_response(self, query: dict[str, str] | None = None) -> bytes:
        """The club, filtered the way the club-search screen asks for it.

        Its parameter names are not the market's: `level`, `nation`, `league`,
        `team`, `position`, `count`. Ignoring them returned the whole club for
        every search, so looking for a Cameroonian centre back listed everyone.
        """
        items = self.items
        if query:

            def number(key: str) -> int | None:
                try:
                    return int(query[key])
                except (KeyError, TypeError, ValueError):
                    return None

            level = (query.get("level") or "").strip().lower()
            position = (query.get("position") or "").strip()
            nation, league = number("nation"), number("league")
            team = number("team")
            kind = (query.get("type") or "").strip().lower()

            def wanted(item: dict) -> bool:
                if kind and kind not in ("any", ""):
                    if item.get("itemType") != kind:
                        return False
                if position and position not in ("any", ""):
                    if item.get("preferredPosition") != position:
                        return False
                if level and level not in ("any", ""):
                    if level not in (item.get("rarity") or "").lower():
                        return False
                for value, field in (
                    (nation, "nation"),
                    (league, "leagueId"),
                    (team, "teamid"),
                ):
                    if value not in (None, -1) and item.get(field) != value:
                        return False
                return True

            items = [item for item in items if wanted(item)]
            count = number("count")
            if count:
                items = items[:count]
        return json.dumps({"itemData": items}, separators=(",", ":")).encode()

    def squad_list_response(self, name: str) -> bytes:
        rating = round(sum(item["rating"] for item in self.squad[:11]) / 11)
        return json.dumps(
            {
                "squad": [
                    {
                        "id": 1,
                        "squadName": name,
                        "rating": rating,
                        "chemistry": 100,
                        "formation": FORMATION,
                    }
                ]
            },
            separators=(",", ":"),
        ).encode()

    def active_squad_response(self, name: str) -> bytes:
        players = []
        for index, item in enumerate(self.squad):
            players.append(
                {
                    "index": index,
                    "itemData": item,
                    # Shirt numbers for the eleven who start; the bench carries
                    # zero, as retail does.
                    "kitNumber": index + 1 if index < 11 else 0,
                }
            )
        return json.dumps(
            {
                "personaId": 0,
                "id": 1,
                "squadName": name,
                "formation": FORMATION,
                "players": players,
                "manager": [],
                "actives": _presentation_items(),
            },
            separators=(",", ":"),
        ).encode()

    def purchased_items_response(self) -> bytes:
        return b'{"duplicateItemIdList":[],"itemData":[]}'


# -- the transfer market ---------------------------------------------------
#
# The club holds 92 cards. The catalogue holds 14019. The market is where the
# difference becomes visible: it is the one screen whose job is to show players
# you do not own.
#
# Listings are generated from the catalogue on demand rather than held, because
# 14019 standing auctions is not a market, it is a phone book. Each search
# returns the best matches for its filters.

AUCTION_DURATION = 3600
# A day, so a listing does not lapse while the screen is being read.
AUCTION_WINDOW = 86400


def _now() -> int:
    return int(time.time())


MARKET_ITEM_ID_BASE = 1_800_000_000
MARKET_TRADE_ID_BASE = 1_900_000_000


def _price_for(rating: int, rareflag: int) -> int:
    """A plausible asking price, so the market is not uniformly free."""
    base = max(150, (rating - 40) ** 2 * 3)
    if rareflag:
        base *= 2
    return int(round(base / 50) * 50)


class CardCatalogue:
    """Every card in the game, searchable."""

    def __init__(self, path: Path = CARD_CATALOGUE) -> None:
        self.cards: list[dict] = []
        if path.exists():
            self.cards = [
                card
                for card in json.loads(path.read_text()).get("cards", [])
                if _card_resolves(card)
            ]
        # Listings are generated per search, so a bid arriving later refers to
        # a trade id that no longer exists anywhere unless it is remembered.
        self.served: dict[int, dict] = {}
        # Cards already bought. Without this the market regenerates the same
        # listing on the next search and the player you just paid for is still
        # sitting there for sale.
        self.sold: set[int] = set()

    def search(self, query: dict[str, str]) -> tuple[list[dict], int]:
        """Filtered, sorted, paged. Returns the page and the full match count.

        The market's parameter names are its own -- `lev` not `level`,
        `definitionId` for one specific player, `start` and `num` for paging.
        Filtering on the names the club search uses meant none of them applied,
        and ignoring `start` meant every page returned the same forty cards.
        """

        def number(key: str) -> int | None:
            value = query.get(key)
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        definition = number("definitionId") or number("maskedDefId")
        level = (query.get("lev") or query.get("level") or "").strip().lower()
        position = (query.get("pos") or query.get("position") or "").strip()
        nation = number("nat") if query.get("nat") else number("nation")
        league = number("leag") if query.get("leag") else number("league")
        team = number("team")
        min_rating, max_rating = number("minb"), number("maxb")

        def wanted(card: dict) -> bool:
            if definition and card.get("assetId") != definition:
                return False
            if level and level not in ("any", ""):
                if level not in (card.get("rarity") or "").lower():
                    return False
            if position and position not in ("any", ""):
                if card.get("position") != position:
                    return False
            for value, field in (
                (nation, "nationId"),
                (league, "leagueId"),
                (team, "clubId"),
            ):
                if value not in (None, -1) and card.get(field) != value:
                    return False
            rating = card.get("rating", 0)
            if min_rating is not None and rating < min_rating:
                return False
            if max_rating is not None and rating > max_rating:
                return False
            return True

        matches = [
            card
            for card in self.cards
            if wanted(card) and card["assetId"] not in self.sold
        ]
        matches.sort(key=lambda card: (-card.get("rating", 0), card.get("name", "")))

        start = number("start") or 0
        count = number("num") or number("count") or 20
        return matches[start : start + count], len(matches)

    def auctions(self, query: dict[str, str], coins: int | None = None) -> bytes:
        page, total = self.search(query)
        listings = []
        try:
            offset = int(query.get("start") or 0)
        except ValueError:
            offset = 0
        for index, card in enumerate(page):
            price = _price_for(card.get("rating", 0), card.get("rareflag", 0))
            item = _player_item(
                item_id=MARKET_ITEM_ID_BASE + offset + index,
                asset_id=card["assetId"],
                rating=card.get("rating", 0),
                rare=card.get("rareflag", 0),
                play_style=0,
                team_id=card.get("clubId", 0),
                attributes=card.get("attributes", [0] * 6),
                position=card.get("position") or FALLBACK_POSITION,
                # "forSale" marks a card *you* have listed, and the screen
                # offers no bid or buy on your own listing -- pressing A did
                # nothing at all. A card on someone else's auction is "free".
                item_state="free",
                nation=card.get("nationId", 0),
                league=card.get("leagueId", 0),
                rarity=card.get("rarity", ""),
            )
            item["untradeable"] = False
            item["owners"] = 1
            item["lastSalePrice"] = 0
            listing = {
                    # Order matters. These parsers read a stream of members and
                    # an unrecognised one can end the object early, taking
                    # everything after it with it -- which is consistent with
                    # the prices showing (they come first) while "Temps
                    # restant" stayed blank and the actions panel opened empty.
                    #
                    # So the timing goes near the front, and the stray "id"
                    # member this code used to add -- which is not part of an
                    # auction record -- is gone.
                    "tradeId": MARKET_TRADE_ID_BASE + offset + index,
                    "tradeState": "active",
                    # Buying failed with "la liste a expiré" while the panel
                    # itself worked, so the expiry check is what refuses it.
                    # The bounds were this machine's clock, and the console has
                    # been offline for years -- its clock is not this one's, so
                    # any absolute comparison is a coin toss.
                    #
                    # So: a window that cannot be outside whatever the console
                    # believes the time is, and a long relative countdown.
                    # EXPIRE_TIME is the member CardsDLL names beside
                    # FUT_AUCTION_EXPIRED, and this server had never sent it --
                    # expires, startTime and endtime are none of them. Sent
                    # alongside the others rather than instead of them, since
                    # those are what made the Actions entry appear.
                    "expires": AUCTION_WINDOW,
                    "EXPIRE_TIME": AUCTION_WINDOW,
                    "expireTime": AUCTION_WINDOW,
                    "startTime": 0,
                    "endtime": 2147483647,
                    "buyNowPrice": price,
                    "startingBid": max(150, price // 2),
                    "currentBid": 0,
                    "offers": 0,
                    "watched": False,
                    "bidState": "none",
                    "tradeOwner": False,
                    "sellerName": "FUT",
                    "sellerEstablished": 2013,
                    "sellerId": 1,
                    "confidenceValue": 100,
                    "itemData": item,
            }
            self.served[listing["tradeId"]] = listing
            listings.append(listing)
        # `total` is the size of the whole result set, not of this page: it is
        # what the screen pages against.
        document = {
            "auctionInfo": listings,
            "duplicateItemIdList": [],
            "total": total,
        }
        if coins is not None:
            document.update({"credits": coins, "totalCredits": coins, "coins": coins})
        return json.dumps(document, separators=(",", ":")).encode()

    def status_for(self, trade_ids: list[int], coins: int) -> bytes:
        """Answer /trade/status?tradeIds=... with those auctions.

        The client polls this for the specific auction it is about to bid on.
        Answering with an empty list -- whatever was asked -- told it nothing
        about that auction, and it refused with "Auction state is invalid for
        bidding". The listing has to come back, by id.
        """
        found = [
            self.served[trade_id]
            for trade_id in trade_ids
            if trade_id in self.served
        ]
        return json.dumps(
            {
                "auctionInfo": found,
                "duplicateItemIdList": [],
                "total": len(found),
                "credits": coins,
                "totalCredits": coins,
                "coins": coins,
            },
            separators=(",", ":"),
        ).encode()


    def bid(self, trade_id: int, amount: int, wallet: "Wallet") -> tuple[bytes, dict | None]:
        """Bid on, or buy outright, a listing this server served earlier.

        Returns the reply and the item won, if the bid took it. A bid at or
        above the buy-now price ends the auction immediately, which is how the
        Buy Now button behaves; anything less is recorded as the standing bid.
        """
        listing = self.served.get(trade_id)
        if listing is None:
            return (
                json.dumps(
                    {"reason": "INVALID_REQUEST", "tradeId": trade_id},
                    separators=(",", ":"),
                ).encode(),
                None,
            )
        buy_now = int(listing.get("buyNowPrice") or 0)
        if amount > wallet.coins:
            return (
                json.dumps(
                    {"reason": "INSUFFICIENT_COINS", "credits": wallet.coins},
                    separators=(",", ":"),
                ).encode(),
                None,
            )
        wallet.debit(amount)
        won = buy_now and amount >= buy_now
        listing = dict(listing)
        listing["currentBid"] = amount
        listing["bidState"] = "highest"
        listing["tradeState"] = "closed" if won else "active"
        listing["offers"] = int(listing.get("offers") or 0) + 1
        listing["credits"] = wallet.coins
        listing["totalCredits"] = wallet.coins
        listing["coins"] = wallet.coins
        self.served[trade_id] = listing
        item = listing.get("itemData") if won else None
        if won and isinstance(item, dict) and item.get("assetId"):
            self.sold.add(int(item["assetId"]))
        return json.dumps(listing, separators=(",", ":")).encode(), item



# -- the coin balance ------------------------------------------------------
#
# The header reads its balance from whatever response last carried it, not at
# login: it showed a clean zero until the first quick sell, then uninitialised
# memory, because our quick-sell reply was an empty object and the parser never
# wrote the field.
#
# `totalCredits` is in CardsDLL's JSON member table, next to `total` and
# `totalGames`. `credits` and `coins` go out beside it: an unrecognised sibling
# at the top level is skipped, so naming all three costs nothing and a wrapper
# would have broken the parse, as {"userInfo":{...}} did.

STARTING_COINS = 1_000_000


class Wallet:
    """The club's coin balance, held for the life of the server."""

    def __init__(self, coins: int = STARTING_COINS) -> None:
        self.coins = coins

    def credit(self, amount: int) -> int:
        self.coins = max(0, self.coins + int(amount))
        return self.coins

    def debit(self, amount: int) -> int:
        return self.credit(-abs(int(amount)))

    def auction_state(self) -> bytes:
        """An empty trade list that still reports the balance."""
        return json.dumps(
            {
                "auctionInfo": [],
                "duplicateItemIdList": [],
                "total": 0,
                "credits": self.coins,
                "totalCredits": self.coins,
                "coins": self.coins,
            },
            separators=(",", ":"),
        ).encode()

    def credits_response(self) -> bytes:
        """FutUserCreditsServerResponse, as the native parser reads it.

        The currency names are compared against the literal lower-case strings
        `coins` and `points`. Sending "COINS" -- which is what the PC
        reference's fixture uses -- matches nothing, so the balance is never
        written and the header keeps whatever it had.

        `unopenedPacks` has to be an object, not an array: the parser descends
        into it for preOrderPacks/recoveredPacks, and an array leaves it walking
        the wrong token type.
        """
        return json.dumps(
            {
                "credits": self.coins,
                "currencies": [
                    {"name": "coins", "funds": self.coins, "finalFunds": self.coins},
                    {"name": "points", "funds": 0, "finalFunds": 0},
                ],
                "unopenedPacks": {"preOrderPacks": 0, "recoveredPacks": 0},
            },
            separators=(",", ":"),
        ).encode()

    def user_info(self, club_name: str, club_abbr: str) -> bytes:
        """FutGetUserInfo, flat -- there is no `userInfo` wrapper.

        Wrapping it is what made the club header print 0xCDCDCDCD: the parser
        did not recognise the shape and never wrote the fields at all.
        """
        return json.dumps(
            {
                "personaId": 0,
                "clubName": club_name,
                "clubAbbr": club_abbr,
                "clubNameChangeAllowed": False,
                "established": 2013,
                "divisionOffline": 10,
                "divisionOnline": 10,
                "won": 0,
                "draw": 0,
                "loss": 0,
                "seasonTicket": False,
                "fifaPointsFromLastYear": 0,
                "fifaPointsTransferredStatus": 0,
                # Aliases some of the UI binders read instead.
                "coins": self.coins,
                "credits": self.coins,
                "points": 0,
                "fifaPoints": 0,
            },
            separators=(",", ":"),
        ).encode()

    def response(self) -> bytes:
        return json.dumps(
            {
                "totalCredits": self.coins,
                "credits": self.coins,
                "coins": self.coins,
            },
            separators=(",", ":"),
        ).encode()


# -- opening a pack --------------------------------------------------------

# The nine packs FIFA 14 sells. Tier decides which cards can come out, count
# how many, and rares how many of them are rare -- a gold pack that draws
# bronze cards is not a gold pack.
PACK_SPECS: dict[int, dict] = {
    103: {"name": "Bronze Pack", "tier": "bronze", "coins": 400, "points": 0,
          "count": 12, "rares": 1, "premium": False, "group": "Bronze Packs"},
    104: {"name": "Premium Bronze Pack", "tier": "bronze", "coins": 750,
          "points": 0, "count": 12, "rares": 3, "premium": True,
          "group": "Bronze Packs"},
    203: {"name": "Silver Pack", "tier": "silver", "coins": 2500, "points": 50,
          "count": 12, "rares": 1, "premium": False, "group": "Silver Packs"},
    204: {"name": "Premium Silver Pack", "tier": "silver", "coins": 3750,
          "points": 75, "count": 12, "rares": 3, "premium": True,
          "group": "Silver Packs"},
    303: {"name": "Gold Pack", "tier": "gold", "coins": 5000, "points": 100,
          "count": 12, "rares": 1, "premium": False, "group": "Gold Packs"},
    304: {"name": "Premium Gold Pack", "tier": "gold", "coins": 7500,
          "points": 150, "count": 12, "rares": 3, "premium": True,
          "group": "Gold Packs"},
    305: {"name": "Jumbo Gold Pack", "tier": "gold", "coins": 10000,
          "points": 0, "count": 24, "rares": 7, "premium": True,
          "group": "Gold Packs"},
    306: {"name": "Gold Players Pack", "tier": "gold", "coins": 15000,
          "points": 0, "count": 12, "rares": 1, "premium": False,
          "group": "Gold Packs"},
    307: {"name": "Premium Gold Players Pack", "tier": "gold", "coins": 25000,
          "points": 0, "count": 12, "rares": 3, "premium": True,
          "group": "Gold Packs"},
}

GOLD_PACK_ID = 304
GOLD_PACK_PRICE = PACK_SPECS[GOLD_PACK_ID]["coins"]
PACK_ITEM_ID_BASE = 1_950_000_000

# Which ratings belong to which tier, so a bronze pack cannot hand you Messi.
TIER_RATINGS = {
    "bronze": (0, 64),
    "silver": (65, 74),
    "gold": (75, 99),
}


def store_catalogue(timestamp: int = 2147483647) -> bytes:
    """Every pack, priced, grouped and buyable."""
    purchases = []
    for index, (pack_id, spec) in enumerate(sorted(PACK_SPECS.items())):
        currencies = [
            {"name": "coins", "funds": spec["coins"], "finalFunds": spec["coins"]}
        ]
        if spec["points"]:
            currencies.append(
                {
                    "name": "points",
                    "funds": spec["points"],
                    "finalFunds": spec["points"],
                }
            )
        # The asset id names the tier's artwork, not the pack: the PC
        # reference ships assetId 3 for the gold pack. Deriving it from the
        # pack id resolved to nothing and the bronze tiles drew NOT FOUND.
        tier_asset = {"bronze": 1, "silver": 2, "gold": 3}[spec["tier"]]
        purchases.append(
            {
                "id": pack_id,
                "assetId": tier_asset,
                "actionType": "CREATEPACK",
                "packType": "CARDPACK",
                "description": f"FUT_STORE_PACK_{pack_id}_DESC",
                "displayGroup": {"priority": index, "value": spec["tier"]},
                "displayGroupAssetId": tier_asset,
                "displayGroupUseDefaultImage": True,
                "useDefaultImage": True,
                "isPremium": spec["premium"],
                "dealType": "REGULAR",
                "saleType": "NONE",
                "state": "active",
                "visible": 1,
                "sortPriority": index,
                "currencies": currencies,
            }
        )
    return json.dumps(
        {"purchase": purchases, "timestamp": timestamp}, separators=(",", ":")
    ).encode()


class PackShop:
    """Sells any pack in the catalogue, and draws cards that match its tier."""

    def __init__(self, catalogue: "CardCatalogue", wallet: "Wallet") -> None:
        self.catalogue = catalogue
        self.wallet = wallet
        self.purchases = 0
        # Cards drawn but not yet acknowledged by the client. The purchased
        # items endpoint reports these, which is how they reach the club.
        self.pending: list[dict] = []
        self._pools: dict[str, list[dict]] = {}
        for tier, (low, high) in TIER_RATINGS.items():
            self._pools[tier] = [
                card
                for card in catalogue.cards
                if low <= card.get("rating", 0) <= high
            ]

    def spec(self, pack_id: int) -> dict | None:
        return PACK_SPECS.get(int(pack_id))

    def price(self, pack_id: int) -> int:
        spec = self.spec(pack_id)
        return int(spec["coins"]) if spec else GOLD_PACK_PRICE

    def can_afford(self, pack_id: int = GOLD_PACK_ID) -> bool:
        return self.wallet.coins >= self.price(pack_id)

    def open_pack(
        self, pack_id: int = GOLD_PACK_ID, rng: random.Random | None = None
    ) -> bytes:
        rng = rng or random.Random()
        spec = self.spec(pack_id) or PACK_SPECS[GOLD_PACK_ID]
        self.wallet.debit(int(spec["coins"]))
        self.purchases += 1

        pool = self._pools.get(spec["tier"]) or self.catalogue.cards
        rares = [card for card in pool if card.get("rareflag")]
        commons = [card for card in pool if not card.get("rareflag")] or pool

        drawn = []
        for slot in range(int(spec["count"])):
            # The rare slots come first, as retail does -- a pack that promises
            # three rares has to actually contain three.
            source = rares if slot < int(spec["rares"]) and rares else commons
            card = rng.choice(source)
            item = _player_item(
                item_id=PACK_ITEM_ID_BASE + self.purchases * 100 + slot,
                asset_id=card["assetId"],
                rating=card.get("rating", 0),
                rare=card.get("rareflag", 0),
                play_style=0,
                team_id=card.get("clubId", 0),
                attributes=card.get("attributes", [0] * 6),
                position=card.get("position") or FALLBACK_POSITION,
                item_state="new",
                nation=card.get("nationId", 0),
                league=card.get("leagueId", 0),
                rarity=card.get("rarity", ""),
            )
            item["untradeable"] = False
            drawn.append(item)
        self.pending.extend(drawn)
        return json.dumps(
            {
                "numberItems": len(drawn),
                "purchasedPackId": int(pack_id),
                "itemList": drawn,
                "duplicateItemIdList": [],
                "credits": self.wallet.coins,
                "totalCredits": self.wallet.coins,
                "coins": self.wallet.coins,
            },
            separators=(",", ":"),
        ).encode()

    def purchased_items(self) -> bytes:
        return json.dumps(
            {
                "duplicateItemIdList": [],
                "itemData": self.pending,
                "credits": self.wallet.coins,
                "totalCredits": self.wallet.coins,
                "coins": self.wallet.coins,
            },
            separators=(",", ":"),
        ).encode()

    def refused(self) -> bytes:
        return json.dumps({"reason": "INSUFFICIENT_COINS"}, separators=(",", ":")).encode()


# -- what you can do with a card -------------------------------------------
#
# Retail pile numbers. Squad membership is not a pile: players in the eleven
# are still owned in the club.
PILE_TRANSFER = 5
PILE_PURCHASED = 6
PILE_CLUB = 7


class CardActions:
    """Moving cards between piles, and quick-selling them."""

    def __init__(
        self,
        shop: "PackShop",
        wallet: "Wallet",
        inventory: "ClubInventory | None" = None,
    ) -> None:
        self.shop = shop
        self.wallet = wallet
        self.inventory = inventory
        # Cards taken out of a pack and kept. When an inventory is attached,
        # this *is* its item list -- otherwise a card sent to the club is held
        # here and never appears in the club, which is exactly how it looked:
        # the send succeeded, the card vanished.
        self.club: list[dict] = []
        # Pile 5: cards set aside to be listed, not yet on the market.
        self.transfer: list[dict] = []
        # Cards actually listed, keyed by trade id.
        self.listings: dict[int, dict] = {}
        self._next_trade_id = 2_000_000_000

    def _take_pending(self, item_id: int) -> dict | None:
        for index, item in enumerate(self.shop.pending):
            if item["id"] == item_id:
                return self.shop.pending.pop(index)
        return None

    def move(self, document: dict) -> bytes:
        """PUT /item -- send to club, or to the transfer list.

        The body is {"itemData":[{"id":N,"pile":7}, ...]} and each entry has to
        be acknowledged individually. Answering with a club search, which is
        what the old fixture did, acknowledges nothing: the card stays in the
        pack screen and the button appears dead.
        """
        entries = document.get("itemData") if isinstance(document, dict) else None
        results = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            try:
                item_id = int(entry.get("id") or entry.get("itemId") or 0)
            except (TypeError, ValueError):
                continue
            pile = entry.get("pile", PILE_CLUB)
            try:
                pile = int(pile)
            except (TypeError, ValueError):
                pile = PILE_CLUB
            item = self._take_pending(item_id)
            if item is None:
                for index, owned in enumerate(self.club):
                    if owned["id"] == item_id:
                        item = self.club.pop(index)
                        break
            if item is not None:
                if pile == PILE_TRANSFER:
                    # Set aside to be listed. It leaves the club until it is
                    # either listed or moved back -- taking it out of the
                    # inventory too, or the card shows in both places at once,
                    # which is what looked like a duplicate.
                    item["itemState"] = "forSale"
                    self._forget(item_id)
                    if not any(held["id"] == item_id for held in self.transfer):
                        self.transfer.append(item)
                else:
                    item["itemState"] = "free"
                    self._keep(item)
            results.append(
                {
                    "id": item_id,
                    "success": True,
                    "reason": "",
                    "errorCode": 0,
                    "pile": pile,
                }
            )
        return json.dumps({"itemData": results}, separators=(",", ":")).encode()

    def discard_many(self, item_ids: list[int]) -> bytes:
        """Quick sell one card or a whole pack at once.

        The client sends {"itemId":[...]} -- always a list, even for one card,
        and twelve entries long when you sell a pack outright. Reading it as a
        single integer produced no id at all, so the reply named no item and the
        screen raised an error.
        """
        sold, awarded = [], 0
        for item_id in item_ids:
            item = self._take_pending(item_id)
            if item is None:
                for index, owned in enumerate(self.club):
                    if owned["id"] == item_id:
                        item = self.club.pop(index)
                        break
            if item is not None:
                self._forget(item_id)
            awarded += int(item["discardValue"]) if item else 200
            sold.append({"id": item_id})
        self.wallet.credit(awarded)
        return json.dumps(
            {"totalCredits": awarded, "items": sold},
            separators=(",", ":"),
        ).encode()

    def discard(self, item_id: int | None = None) -> bytes:
        """Quick sell.

        `totalCredits` here is what this sale paid, not the resulting balance --
        the absolute figure comes from /user/credits. Returning the whole
        balance made the sale appear to pay tens of thousands.
        """
        item = self._take_pending(item_id) if item_id else None
        if item is None:
            for index, owned in enumerate(self.club):
                if item_id and owned["id"] == item_id:
                    item = self.club.pop(index)
                    break
        awarded = int(item["discardValue"]) if item else 200
        self.wallet.credit(awarded)
        return json.dumps(
            {
                "totalCredits": awarded,
                "items": [{"id": item_id}] if item_id else [],
            },
            separators=(",", ":"),
        ).encode()


    def _forget(self, item_id: int) -> None:
        """Drop a card from the club, wherever it is held."""
        self.club = [held for held in self.club if held["id"] != item_id]
        if self.inventory is not None:
            self.inventory.items = [
                held for held in self.inventory.items if held["id"] != item_id
            ]

    def _keep(self, item: dict) -> None:
        """Put a card in the club, once.

        A club holds one of any given card: the same item id arriving twice is
        the same card, and the same asset in the same version is a duplicate
        the club has no room for either.
        """
        signature = (item.get("assetId"), item.get("rareflag"))
        for held in self.club:
            if held["id"] == item["id"]:
                return
            if (held.get("assetId"), held.get("rareflag")) == signature:
                return
        self.club.append(item)
        if self.inventory is not None:
            for held in self.inventory.items:
                if held["id"] == item["id"]:
                    return
                if (held.get("assetId"), held.get("rareflag")) == signature:
                    return
            self.inventory.items.append(item)

    def _find(self, item_id: int) -> dict | None:
        for pool in (self.transfer, self.club, self.shop.pending):
            for item in pool:
                if item["id"] == item_id:
                    return item
        return None

    def list_for_sale(self, document: dict) -> bytes:
        """POST /auctionhouse -- put a card on the market.

        The body names the item and the prices; the reply has to hand back a
        trade id, because that is what the trade pile and every later bid or
        withdrawal refer to.
        """
        item_data = document.get("itemData") if isinstance(document, dict) else None
        if isinstance(item_data, list):
            item_data = item_data[0] if item_data else None
        item_id = None
        if isinstance(item_data, dict):
            item_id = item_data.get("id") or item_data.get("itemId")
        if item_id is None:
            item_id = document.get("itemId") or document.get("id")
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            item_id = None

        def price(key: str, fallback: int) -> int:
            try:
                return int(document.get(key) or fallback)
            except (TypeError, ValueError):
                return fallback

        item = self._find(item_id) if item_id else None
        starting = price("startingBid", 150)
        buy_now = price("buyNowPrice", max(200, starting * 2))
        duration = price("duration", 3600)

        self._next_trade_id += 1
        trade_id = self._next_trade_id
        listing = {
            "tradeId": trade_id,
            "id": trade_id,
            "itemData": item or {"id": item_id},
            "tradeState": "active",
            "startingBid": starting,
            "buyNowPrice": buy_now,
            "currentBid": 0,
            "offers": 0,
            "watched": False,
            "bidState": "none",
            "tradeOwner": True,
            "expires": duration,
            "sellerName": "Fondateur FUT",
            "sellerEstablished": 2013,
            "sellerId": 0,
            "confidenceValue": 100,
        }
        self.listings[trade_id] = listing
        for pool in (self.transfer, self.club):
            for index, owned in enumerate(pool):
                if owned["id"] == item_id:
                    pool.pop(index)
                    break
        return json.dumps(listing, separators=(",", ":")).encode()

    def trade_pile(self, coins: int) -> bytes:
        """Everything currently listed, plus the balance the header reads."""
        return json.dumps(
            {
                "auctionInfo": list(self.listings.values()),
                "duplicateItemIdList": [],
                "total": len(self.listings),
                "credits": coins,
                "totalCredits": coins,
                "coins": coins,
            },
            separators=(",", ":"),
        ).encode()

    def withdraw(self, trade_id: int) -> bytes:
        """Pull a listing back; the card returns to the transfer pile."""
        listing = self.listings.pop(trade_id, None)
        if listing and isinstance(listing.get("itemData"), dict):
            item = listing["itemData"]
            if "assetId" in item:
                self.transfer.append(item)
        return json.dumps({"id": trade_id}, separators=(",", ":")).encode()


# -- everything a club owns that is not a player ---------------------------
#
# The club screens have tabs for consumables, club items and staff, and a
# search that filters on them. Serving players only leaves those tabs empty and
# their filters inert.

CLUB_ITEM_ID_BASE = 1_750_000_000

# Contracts and fitness are what a club actually runs out of; the rest are the
# cosmetics the club and stadium screens present.
CONSUMABLE_KINDS = [
    ("contract", "Contract", [(1, 7), (2, 15), (3, 28)]),
    ("fitness", "Fitness", [(1, 25), (2, 50), (3, 75)]),
    ("healing", "Healing", [(1, 1), (2, 3), (3, 7)]),
    ("training", "Training", [(1, 1), (2, 3), (3, 5)]),
    ("position", "Position", [(1, 1)]),
    ("playStyle", "Chemistry Style", [(1, 1), (2, 1), (3, 1)]),
]

CLUB_ITEM_KINDS = [
    ("kit", 14, 6300000, 4),
    ("badge", 241, 6000000, 4),
    ("stadium", 6, 6200000, 3),
    ("ball", 23, 8120091, 3),
    ("manager", 1, 6100000, 2),
    ("staff", 2, 6150000, 3),
]


def _club_extras() -> list[dict]:
    """Consumables, kits, badges, stadiums, balls and staff."""
    items: list[dict] = []
    next_id = CLUB_ITEM_ID_BASE

    for kind, label, grades in CONSUMABLE_KINDS:
        for index, (grade, amount) in enumerate(grades):
            items.append(
                {
                    "id": next_id,
                    "assetId": 1000 + index,
                    "resourceId": RESOURCE_VERSION | (1000 + index),
                    "rating": 0,
                    "itemType": "training" if kind == "training" else kind,
                    "itemState": "free",
                    "cardsubtypeid": grade,
                    "discardValue": 0,
                    "lastSalePrice": 0,
                    "timestamp": 1,
                    "untradeable": False,
                    "rareflag": 1 if grade > 1 else 0,
                    "consumableType": kind,
                    "consumableLabel": label,
                    "amount": amount,
                    "count": 5,
                }
            )
            next_id += 1

    for kind, asset, resource, count in CLUB_ITEM_KINDS:
        for index in range(count):
            items.append(
                {
                    "id": next_id,
                    "assetId": asset + index,
                    "resourceId": resource + index,
                    "rating": 0,
                    "itemType": kind,
                    "itemState": "free",
                    "discardValue": 0,
                    "lastSalePrice": 0,
                    "timestamp": 1,
                    "untradeable": False,
                    "rareflag": 0,
                }
            )
            next_id += 1
    return items


# -- the game modes --------------------------------------------------------
#
# Seasons, tournaments and Team of the Week. Each of these screens refuses an
# empty list the way fcc_login2 refuses an empty squad, so "none available" is
# not a neutral answer -- it is the error the screen reports.

SEASON_DIVISIONS = [
    (10, "Division 10", 4, 2, 400),
    (9, "Division 9", 4, 2, 500),
    (8, "Division 8", 5, 2, 650),
    (7, "Division 7", 5, 3, 800),
    (6, "Division 6", 6, 3, 1000),
    (5, "Division 5", 6, 3, 1300),
    (4, "Division 4", 7, 4, 1700),
    (3, "Division 3", 7, 4, 2200),
    (2, "Division 2", 8, 4, 3000),
    (1, "Division 1", 10, 5, 5000),
]

TOURNAMENTS = [
    (1, "Coupe des Fondateurs", "bronze", 1000, 5),
    (2, "Coupe Nationale", "silver", 2500, 5),
    (3, "Coupe des Champions", "gold", 5000, 5),
    (4, "Coupe du Monde des Clubs", "gold", 7500, 6),
]


def seasons_response() -> bytes:
    return json.dumps(
        {
            "seasons": [
                {
                    "seasonId": division,
                    "division": division,
                    "name": name,
                    "matchesPlayed": 0,
                    "matchesToPlay": matches,
                    "pointsToPromote": promote,
                    "points": 0,
                    "won": 0,
                    "draw": 0,
                    "lost": 0,
                    "coinsPerWin": coins,
                    "trophiesWon": 0,
                }
                for division, name, matches, promote, coins in SEASON_DIVISIONS
            ]
        },
        separators=(",", ":"),
    ).encode()


def season_user_response(division: int = 10) -> bytes:
    """Where the club currently stands. Starts in the bottom division."""
    entry = next(
        (row for row in SEASON_DIVISIONS if row[0] == division), SEASON_DIVISIONS[0]
    )
    _, name, matches, promote, coins = entry
    return json.dumps(
        {
            "seasonId": division,
            "division": division,
            "name": name,
            "matchesPlayed": 0,
            "matchesToPlay": matches,
            "pointsToPromote": promote,
            "points": 0,
            "won": 0,
            "draw": 0,
            "lost": 0,
            "coinsPerWin": coins,
            "trophiesWon": 0,
            "relegated": False,
            "promoted": False,
        },
        separators=(",", ":"),
    ).encode()


def tournaments_response() -> bytes:
    return json.dumps(
        {
            "tournament": [
                {
                    "tournamentId": identifier,
                    "name": name,
                    "level": level,
                    "prize": prize,
                    "rounds": rounds,
                    "currentRound": 0,
                    "entryFee": 0,
                    "active": True,
                    "won": 0,
                }
                for identifier, name, level, prize, rounds in TOURNAMENTS
            ]
        },
        separators=(",", ":"),
    ).encode()


def active_tournaments_response() -> bytes:
    return json.dumps(
        {"tournamentId": [row[0] for row in TOURNAMENTS]}, separators=(",", ":")
    ).encode()


TOTW_FILE = Path(__file__).resolve().parent / "fifa14_totw.json"


def _totw_asset_ids() -> list[int]:
    """The real Team of the Week, if it was fetched.

    wefut publishes one at /squad/1, titled "TOTW 1". The squads after it are
    not the following weeks -- that path is a public gallery of user-built
    sides -- so only pages that name themselves TOTW are kept, and the rest of
    the screen falls back to the best rare cards in the catalogue.
    """
    if not TOTW_FILE.exists():
        return []
    squads = json.loads(TOTW_FILE.read_text()).get("squads", [])
    return list(squads[0]["assetIds"]) if squads else []


def totw_response(catalogue: "CardCatalogue", size: int = 23) -> bytes:
    """Team of the Week."""
    by_asset = {card["assetId"]: card for card in catalogue.cards}
    best = [
        by_asset[asset] for asset in _totw_asset_ids() if asset in by_asset
    ]
    if len(best) < size:
        # Fill the bench from the catalogue's best rares, so the squad screen
        # gets a full side rather than a partial one.
        seen = {card["assetId"] for card in best}
        best += [
            card
            for card in catalogue.cards
            if card.get("rareflag")
            and card.get("rating", 0) >= 80
            and card["assetId"] not in seen
        ][: size - len(best)]
    best = best[:size]
    items = []
    for index, card in enumerate(best):
        items.append(
            _player_item(
                item_id=1_850_000_000 + index,
                asset_id=card["assetId"],
                rating=card.get("rating", 0),
                rare=card.get("rareflag", 1),
                play_style=0,
                team_id=card.get("clubId", 0),
                attributes=card.get("attributes", [0] * 6),
                position=card.get("position") or FALLBACK_POSITION,
                item_state="free",
                nation=card.get("nationId", 0),
                league=card.get("leagueId", 0),
                rarity=card.get("rarity", ""),
            )
        )
    return json.dumps(
        {"itemData": items, "formation": FORMATION, "squadName": "Équipe de la semaine"},
        separators=(",", ":"),
    ).encode()


def hub_response(inventory: "ClubInventory", listings: int) -> bytes:
    """The My Club and transfer tiles read their counts from here.

    Both were fixed numbers -- 92 players and no auctions -- so the club tile
    never moved as cards arrived and the market tile always read zero.
    """
    players = sum(
        1 for item in inventory.items if item.get("itemType") == "player"
    )
    return json.dumps(
        {"auctionCount": listings, "clubPlayers": players},
        separators=(",", ":"),
    ).encode()


def club_stats_response(inventory: "ClubInventory") -> bytes:
    """The Mon Club counters: players, rares, staff, stadiums, kits, badges.

    They all read zero because the stats endpoints answered with an empty
    entries list -- a club full of cards reporting nothing owned.

    The key numbers are the screen's own slots; they are recovered from what
    the screen displays rather than from a document, so the mapping is a
    reading and not a certainty. The counts themselves are exact.
    """
    def count(kind: str) -> int:
        return sum(1 for item in inventory.items if item.get("itemType") == kind)

    players = count("player")
    rares = sum(
        1
        for item in inventory.items
        if item.get("itemType") == "player" and item.get("rareflag")
    )
    entries = [
        {"key": 0, "value": players},
        {"key": 1, "value": rares},
        {"key": 2, "value": count("staff") + count("manager")},
        {"key": 3, "value": count("stadium")},
        {"key": 4, "value": count("kit")},
        {"key": 5, "value": count("badge")},
        {"key": 6, "value": count("ball")},
        {"key": 7, "value": 0},
    ]
    return json.dumps({"entries": entries}, separators=(",", ":")).encode()


CONSUMABLE_TYPES = {
    "contract", "fitness", "healing", "training", "position", "playStyle",
}


def consumables_response(inventory: "ClubInventory") -> bytes:
    """The Consommables screen asks here, by category.

    It was a 404, which is why the tab looked empty however many consumables
    the club held.
    """
    items = [
        item
        for item in inventory.items
        if item.get("itemType") in CONSUMABLE_TYPES
        or item.get("consumableType") in CONSUMABLE_TYPES
    ]
    return json.dumps({"itemData": items}, separators=(",", ":")).encode()


def totw_index() -> bytes:
    """The list of Team of the Week squads available to view.

    The screen asks for the TOTW itself and then for this list, and a 404 here
    is what it reports as "aucune Équipe de la semaine disponible" -- the squad
    had already been served successfully.
    """
    squads = []
    if TOTW_FILE.exists():
        squads = json.loads(TOTW_FILE.read_text()).get("squads", [])
    return json.dumps(
        {
            "squad": [
                {
                    "id": index + 1,
                    "squadName": squad.get("name", f"TOTW {index + 1}"),
                    "formation": FORMATION,
                    "rating": 0,
                    "chemistry": 100,
                }
                for index, squad in enumerate(squads)
            ],
            "userInfo": [],
        },
        separators=(",", ":"),
    ).encode()


# -- keeping the club between sessions -------------------------------------
#
# Everything above is rebuilt from the icebreaker packs at every server start,
# so a card sent to the club survived exactly as long as the process did: the
# club counter went back to 92 on the next relaunch and the pack you opened was
# gone. Entering FUT needs a relaunch, so that is every single session.

SAVE_FILE = Path(__file__).resolve().parent.parent / "runtime" / "club-save.json"


class ClubSave:
    """The club's own state, written to disk and reloaded."""

    def __init__(self, path: Path = SAVE_FILE) -> None:
        self.path = path

    def load(self, inventory: "ClubInventory", wallet: "Wallet",
             actions: "CardActions") -> bool:
        if not self.path.exists():
            return False
        try:
            saved = json.loads(self.path.read_text())
        except (ValueError, OSError):
            return False
        wallet.coins = int(saved.get("coins", wallet.coins))
        known = {item["id"] for item in inventory.items}
        for item in saved.get("acquired", []):
            if item["id"] not in known:
                inventory.items.append(item)
                actions.club.append(item)
        for item in saved.get("sold", []):
            inventory.items = [
                held for held in inventory.items if held["id"] != item
            ]
        actions.transfer = list(saved.get("transfer", []))
        actions.listings = {
            int(key): value for key, value in saved.get("listings", {}).items()
        }
        return True

    def save(self, inventory: "ClubInventory", wallet: "Wallet",
             actions: "CardActions") -> None:
        starting = ClubInventory()
        original = {item["id"] for item in starting.items}
        current = {item["id"] for item in inventory.items}
        document = {
            "coins": wallet.coins,
            # Only what differs from the starting club, so the save stays small
            # and a change to the icebreaker packs still flows through.
            "acquired": [
                item for item in inventory.items if item["id"] not in original
            ],
            "sold": sorted(original - current),
            "transfer": actions.transfer,
            "listings": {str(key): value for key, value in actions.listings.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(document, separators=(",", ":")))


def totw_index_with_squad(catalogue: "CardCatalogue") -> bytes:
    """The Team of the Week list, carrying the squad itself as well.

    The screen asks clientdata/totw and then this, and rejected the plain list
    it was given -- the squad had already been served successfully, so what it
    refuses is this document. Which member it reads is not known, so the squad
    summary, the cards and an empty user list all go out together; an
    unrecognised sibling at the top level is skipped.
    """
    index = json.loads(totw_index())
    squad = json.loads(totw_response(catalogue))
    index["itemData"] = squad["itemData"]
    index["formation"] = squad["formation"]
    return json.dumps(index, separators=(",", ":")).encode()
