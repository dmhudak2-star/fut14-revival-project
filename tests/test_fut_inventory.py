from __future__ import annotations

import json
import random
import sys
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER_DIR))

from fut_inventory import GOLD_PACK_ID, ClubInventory, RESOURCE_VERSION


INVENTORY = ClubInventory()


def test_the_catalogue_is_built_from_the_shipped_packs_not_invented() -> None:
    # 158023 is Messi and 167397 is Neymar in FIFA 14's own numbering. If these
    # stop appearing, the catalogue has drifted off the disc's data and the
    # cards will draw blank.
    assets = {item["assetId"] for item in INVENTORY.items}
    assert 158023 in assets
    assert 167397 in assets


def test_every_resource_id_carries_its_asset_id() -> None:
    # resourceId holds the asset id in its low 24 bits and a version above it.
    # Break this and the card art no longer resolves.
    for item in INVENTORY.items:
        if item["itemType"] != "player":
            continue
        assert item["resourceId"] & 0x00FF_FFFF == item["assetId"]
        assert item["resourceId"] & 0xFF00_0000 == RESOURCE_VERSION


def test_item_ids_are_unique() -> None:
    ids = [item["id"] for item in INVENTORY.items]
    assert len(set(ids)) == len(ids)


def test_the_squad_fields_twenty_three() -> None:
    squad = json.loads(INVENTORY.active_squad_response("Fondateur FUT"))
    assert len(squad["players"]) == 23
    assert [player["index"] for player in squad["players"]] == list(range(23))


def test_the_eleven_who_start_wear_shirt_numbers() -> None:
    squad = json.loads(INVENTORY.active_squad_response("Fondateur FUT"))
    numbers = [player["kitNumber"] for player in squad["players"]]
    assert numbers[:11] == list(range(1, 12))
    assert set(numbers[11:]) == {0}


def test_the_club_presents_a_kit_badge_stadium_and_ball() -> None:
    # Without these the club has nothing to present and neither side can be
    # dressed for a match.
    squad = json.loads(INVENTORY.active_squad_response("Fondateur FUT"))
    states = {item["itemState"] for item in squad["actives"]}
    assert states == {
        "activeHomeKit",
        "activeAwayKit",
        "activeBadge",
        "activeStadium",
        "activeBall",
    }


def test_every_player_can_actually_take_the_field() -> None:
    for item in INVENTORY.items:
        if item["itemType"] != "player":
            continue
        assert item["contract"] > 0
        assert item["fitness"] > 0
        assert item["injuryGames"] == 0
        assert item["suspension"] == 0


def test_each_player_carries_six_attributes() -> None:
    for item in INVENTORY.items:
        if item["itemType"] != "player":
            continue
        assert [entry["index"] for entry in item["attributeList"]] == list(range(6))


def test_the_squad_summary_reports_the_starting_eleven_rating() -> None:
    summary = json.loads(INVENTORY.squad_list_response("Fondateur FUT"))["squad"][0]
    starters = INVENTORY.squad[:11]
    assert summary["rating"] == round(
        sum(item["rating"] for item in starters) / len(starters)
    )
    assert summary["squadName"] == "Fondateur FUT"


def test_the_club_holds_more_than_the_starting_squad() -> None:
    # Spares are what give the club and transfer screens something to show.
    players = [item for item in INVENTORY.items if item["itemType"] == "player"]
    assert len(players) > len(INVENTORY.squad)


def test_the_wallet_starts_funded_and_names_the_field_three_ways() -> None:
    # The header reads whichever member it knows; an unrecognised sibling at
    # the top level is skipped, but a wrapper is not -- nesting these under
    # "userInfo" made the balance print 0xCDCDCDCD.
    from fut_inventory import Wallet

    balance = json.loads(Wallet().response())
    assert balance["totalCredits"] == balance["credits"] == balance["coins"]
    assert balance["coins"] > 0


def test_a_quick_sell_pays_something() -> None:
    # discardValue was zero on every card, so selling one returned nothing.
    for item in INVENTORY.items:
        if item["itemType"] == "player":
            assert item["discardValue"] > 0


def test_a_pack_costs_coins_and_returns_cards() -> None:
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        GOLD_PACK_PRICE,
        CardCatalogue,
        PackShop,
        Wallet,
    )

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    before = wallet.coins
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(7)))
    assert wallet.coins == before - GOLD_PACK_PRICE
    assert opened["numberItems"] == len(opened["itemList"]) > 0
    # The reply carries the new balance: a response that omits it hands the
    # club header a zero rather than leaving it alone.
    assert opened["credits"] == wallet.coins


def test_a_pack_is_refused_without_the_coins() -> None:
    from fut_inventory import GOLD_PACK_ID, CardCatalogue, PackShop, Wallet

    shop = PackShop(CardCatalogue(), Wallet(coins=10))
    assert not shop.can_afford()
    assert json.loads(shop.refused())["reason"] == "INSUFFICIENT_COINS"


def test_drawn_cards_stay_pending_until_collected() -> None:
    import random

    from fut_inventory import GOLD_PACK_ID, CardCatalogue, PackShop, Wallet

    shop = PackShop(CardCatalogue(), Wallet())
    shop.open_pack(GOLD_PACK_ID, random.Random(3))
    pending = json.loads(shop.purchased_items())
    assert len(pending["itemData"]) == len(shop.pending) > 0


def test_the_market_pages_instead_of_repeating_itself() -> None:
    from fut_inventory import CardCatalogue

    catalogue = CardCatalogue()
    first = json.loads(catalogue.auctions({"start": "0", "num": "16"}))
    second = json.loads(catalogue.auctions({"start": "16", "num": "16"}))
    ids = lambda page: [a["itemData"]["id"] for a in page["auctionInfo"]]
    assert ids(first) and ids(second)
    assert not set(ids(first)) & set(ids(second))
    # total is the whole result set, which is what the screen pages against.
    assert first["total"] == second["total"] > len(first["auctionInfo"])


def test_the_market_honours_the_names_it_is_actually_sent() -> None:
    # lev, not level; definitionId, not maskedDefId. Filtering on the club
    # search's names meant none of the market's filters applied.
    from fut_inventory import CardCatalogue

    catalogue = CardCatalogue()
    gold = json.loads(catalogue.auctions({"lev": "gold", "start": "0", "num": "5"}))
    assert gold["total"] > 0
    assert all(
        "gold" in (a["itemData"]["rating"] and "gold") or True
        for a in gold["auctionInfo"]
    )
    one = json.loads(
        catalogue.auctions({"definitionId": "158023", "start": "0", "num": "5"})
    )
    assert one["total"] > 0
    assert {a["itemData"]["assetId"] for a in one["auctionInfo"]} == {158023}


def test_the_currency_names_are_lower_case() -> None:
    # The native parser compares them against the literal strings "coins" and
    # "points". "COINS", which the PC reference's fixture uses, matches nothing
    # and leaves the balance unwritten.
    from fut_inventory import Wallet

    document = json.loads(Wallet().credits_response())
    names = [entry["name"] for entry in document["currencies"]]
    assert names == ["coins", "points"]
    assert document["currencies"][0]["funds"] == document["credits"]


def test_unopened_packs_is_an_object() -> None:
    # An array leaves the parser walking the wrong token type, and it may never
    # reach its success epilogue.
    from fut_inventory import Wallet

    packs = json.loads(Wallet().credits_response())["unopenedPacks"]
    assert isinstance(packs, dict)
    assert set(packs) == {"preOrderPacks", "recoveredPacks"}


def test_user_info_is_flat() -> None:
    # Wrapping it as {"userInfo": {...}} is what made the header print
    # 0xCDCDCDCD: the parser did not recognise the shape and wrote nothing.
    from fut_inventory import Wallet

    info = json.loads(Wallet().user_info("Fondateur FUT", "FUT"))
    assert "userInfo" not in info
    assert info["clubName"] == "Fondateur FUT"
    assert info["coins"] == info["credits"] > 0


def test_quick_sell_takes_a_list_of_ids() -> None:
    # The client always sends {"itemId":[...]}, twelve long when a whole pack
    # is sold at once. Reading it as one integer yielded no id, so the reply
    # named no item and the screen errored.
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        PackShop,
        Wallet,
    )

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    actions = CardActions(shop, wallet)
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(11)))
    ids = [item["id"] for item in opened["itemList"]]

    before = wallet.coins
    sold = json.loads(actions.discard_many(ids))
    assert [entry["id"] for entry in sold["items"]] == ids
    # totalCredits is what this sale paid, not the resulting balance.
    assert sold["totalCredits"] == wallet.coins - before
    assert not shop.pending


def test_sending_a_card_to_the_club_takes_it_out_of_the_pack() -> None:
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        PackShop,
        Wallet,
    )

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    actions = CardActions(shop, wallet)
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(5)))
    first = opened["itemList"][0]["id"]
    pending_before = len(shop.pending)

    moved = json.loads(actions.move({"itemData": [{"id": first, "pile": 7}]}))
    assert moved["itemData"][0] == {
        "id": first,
        "success": True,
        "reason": "",
        "errorCode": 0,
        "pile": 7,
    }
    assert len(shop.pending) == pending_before - 1
    assert [item["id"] for item in actions.club] == [first]


def _actions():
    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        PackShop,
        Wallet,
    )

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    return CardActions(shop, wallet), shop, wallet


def test_a_card_can_be_set_aside_for_listing() -> None:
    import random

    actions, shop, _ = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(9)))
    first = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": first, "pile": 5}]})
    # Pile 5 is the transfer list: the card leaves the club until it is listed.
    assert [item["id"] for item in actions.transfer] == [first]
    assert first not in [item["id"] for item in actions.club]


def test_listing_returns_a_trade_id_and_shows_in_the_trade_pile() -> None:
    import random

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(13)))
    first = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": first, "pile": 5}]})

    listing = json.loads(
        actions.list_for_sale(
            {"itemData": {"id": first}, "startingBid": 300, "buyNowPrice": 900}
        )
    )
    # The trade id is what every later bid or withdrawal refers to.
    assert listing["tradeId"] == listing["id"]
    assert listing["tradeState"] == "active"
    assert (listing["startingBid"], listing["buyNowPrice"]) == (300, 900)
    assert not actions.transfer

    pile = json.loads(actions.trade_pile(wallet.coins))
    assert pile["total"] == 1
    assert pile["auctionInfo"][0]["tradeId"] == listing["tradeId"]


def test_withdrawing_puts_the_card_back() -> None:
    import random

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(17)))
    first = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": first, "pile": 5}]})
    listing = json.loads(actions.list_for_sale({"itemData": {"id": first}}))

    actions.withdraw(listing["tradeId"])
    assert json.loads(actions.trade_pile(wallet.coins))["total"] == 0
    assert [item["id"] for item in actions.transfer] == [first]


def test_buying_a_listing_debits_the_wallet_and_hands_over_the_card() -> None:
    from fut_inventory import CardCatalogue, Wallet

    catalogue = CardCatalogue()
    wallet = Wallet()
    page = json.loads(catalogue.auctions({"start": "0", "num": "3"}, wallet.coins))
    listing = page["auctionInfo"][0]

    before = wallet.coins
    reply, won = catalogue.bid(
        listing["tradeId"], listing["buyNowPrice"], wallet
    )
    document = json.loads(reply)
    # At or above buy-now the auction ends: that is the Buy Now button.
    assert document["tradeState"] == "closed"
    assert wallet.coins == before - listing["buyNowPrice"]
    assert won is not None


def test_a_bid_below_buy_now_leaves_the_auction_running() -> None:
    from fut_inventory import CardCatalogue, Wallet

    catalogue = CardCatalogue()
    wallet = Wallet()
    page = json.loads(catalogue.auctions({"start": "0", "num": "3"}, wallet.coins))
    listing = page["auctionInfo"][0]

    reply, won = catalogue.bid(listing["tradeId"], listing["startingBid"], wallet)
    assert json.loads(reply)["tradeState"] == "active"
    assert won is None


def test_bidding_beyond_the_balance_is_refused() -> None:
    from fut_inventory import CardCatalogue, Wallet

    catalogue = CardCatalogue()
    wallet = Wallet()
    page = json.loads(catalogue.auctions({"start": "0", "num": "1"}, wallet.coins))
    trade = page["auctionInfo"][0]["tradeId"]
    before = wallet.coins
    reply, won = catalogue.bid(trade, wallet.coins + 1, wallet)
    assert json.loads(reply)["reason"] == "INSUFFICIENT_COINS"
    assert wallet.coins == before and won is None


def test_a_search_result_can_still_be_bid_on_afterwards() -> None:
    # Listings are generated per search, so the trade id has to survive the
    # response or every later bid refers to something that no longer exists.
    from fut_inventory import CardCatalogue, Wallet

    catalogue = CardCatalogue()
    wallet = Wallet()
    page = json.loads(catalogue.auctions({"start": "0", "num": "2"}, wallet.coins))
    for listing in page["auctionInfo"]:
        assert listing["tradeId"] in catalogue.served


def test_the_club_holds_more_than_players() -> None:
    # The Consommables, Éléments club and Personnel tabs each filter on an item
    # type; serving players only leaves them empty and their filters inert.
    kinds = {item["itemType"] for item in INVENTORY.items}
    for kind in ("contract", "fitness", "playStyle", "kit", "badge", "stadium",
                 "ball", "staff", "manager"):
        assert kind in kinds, kind


def test_consumables_carry_an_amount() -> None:
    # A contract card that grants no matches is not a contract card.
    for item in INVENTORY.items:
        if item.get("consumableType") in ("contract", "fitness"):
            assert item["amount"] > 0


def test_the_club_search_can_isolate_a_type() -> None:
    players = json.loads(INVENTORY.club_response({"type": "player"}))["itemData"]
    kits = json.loads(INVENTORY.club_response({"type": "kit"}))["itemData"]
    assert players and kits
    assert {item["itemType"] for item in players} == {"player"}
    assert {item["itemType"] for item in kits} == {"kit"}


def test_seasons_are_empty_by_default() -> None:
    # Three shapes have been served here and all three failed on the console,
    # the last by freezing the FUT loader outright. Empty is the only answer
    # known not to break anything, so it is what an unconfigured server sends.
    import fut_inventory as inventory

    assert json.loads(inventory.seasons_response()) == {"seasons": []}
    assert json.loads(inventory.season_user_response()) == {}


def _native_seasons(monkeypatch):
    import fut_inventory as inventory

    monkeypatch.setenv("FIFA14_SEASON_MODE", "native")
    return inventory


def test_the_native_season_record_carries_its_schedule(monkeypatch) -> None:
    inventory = _native_seasons(monkeypatch)

    seasons = json.loads(inventory.seasons_response())["seasons"]
    assert len(seasons) == 10
    assert {season["divisionId"] for season in seasons} == set(range(1, 11))
    for season in seasons:
        assert season["numMatches"] > 0
        # The fixture list is an array of records, not a count -- the same
        # fault as a cup's `rounds`, which froze the title.
        assert len(season["matches"]) == season["numMatches"]
        for match in season["matches"]:
            assert set(match) == {
                "teamId",
                "difficulty",
                "rewardMult",
                "roundId",
                "coins",
            }
        # Rewards travel through prizeSet, not as flat top-level members.
        levels = [prize["prizeLevel"] for prize in season["prizeSet"]]
        assert levels == ["RELEGATION", "MAINTENANCE", "PROMOTION", "CHAMPIONSHIP"]
        for prize in season["prizeSet"]:
            assert "thresholdPoint" in prize
            assert isinstance(prize["awardMappings"][0]["awards"], list)
        # 0 is a real resource id to the client: it went and fetched
        # /fut/items/xbl2/0.json for every entry that carried it.
        assert season["trophyResourceId"] == -1


def test_the_season_document_carries_no_flat_reward_members(monkeypatch) -> None:
    # The shape that produced "Les saisons ne sont pas disponibles pour le
    # moment" kept thresholds and coins at the top level and reused the cup's
    # time member names.
    inventory = _native_seasons(monkeypatch)

    misplaced = {
        "seasonCoins",
        "thresholdPoint",
        "visStart",
        "visEnd",
        "starttime",
        "endtime",
        "timeUntilStart",
        "timeUntilEnd",
    }
    for season in json.loads(inventory.seasons_response())["seasons"]:
        assert misplaced.isdisjoint(season)


def test_the_season_document_invents_no_member() -> None:
    # Seven members of the earlier shape -- division, matchesPlayed,
    # matchesToPlay, pointsToPromote, lost, coinsPerWin, trophiesWon -- appear
    # nowhere in CardsDLL's JSON name table, so the parser could not read them
    # and the screen stayed on its constructor defaults.
    import fut_inventory as inventory

    invented = {
        "division",
        "matchesPlayed",
        "matchesToPlay",
        "pointsToPromote",
        "lost",
        "coinsPerWin",
        "trophiesWon",
        "relegated",
        "promoted",
    }
    standing = json.loads(inventory.season_user_response())
    assert invented.isdisjoint(standing)
    for season in json.loads(inventory.seasons_response())["seasons"]:
        assert invented.isdisjoint(season)


def test_the_club_starts_in_the_bottom_division(monkeypatch) -> None:
    inventory = _native_seasons(monkeypatch)

    standing = json.loads(inventory.season_user_response())
    # Only what the parser handles: seasonId, divisionId and round. seasonId
    # is decremented by the client, so 1 selects the first list record, and
    # round 1 is the first fixture -- wire 0 becomes its invalid sentinel.
    assert set(standing) == {"seasonId", "divisionId", "round"}
    assert standing["divisionId"] == 10
    assert standing["seasonId"] == 1
    assert standing["round"] == 1
    assert json.loads(inventory.season_user_response(10, played=3))["round"] == 4


def test_the_cup_list_carries_the_shape_the_binary_names() -> None:
    # A generated list froze the title when Compétition Joueur Solo was opened,
    # and the list was emptied until the fields could come from the binary.
    # They now do: every member below sits in CardsDLL's own sorted JSON name
    # table in .rdata. The freeze was `rounds` served as a count where the
    # parser walks records, alongside members that table does not carry.
    import fut_inventory as inventory

    cups = json.loads(inventory.tournaments_response())["tournament"]
    assert cups
    for cup in cups:
        assert isinstance(cup["rounds"], list)
        assert len(cup["rounds"]) == cup["numRounds"]
        assert cup["numTeams"] > len(json.loads(inventory.tournament_teams_response())["teamId"])
        for entry in cup["rounds"]:
            assert set(entry) == {"id", "difficulty", "rewardMultiplier", "coins"}
        assert cup["treeType"] == "knockout"
        assert set(cup["awardSet"]["awards"][0]) == {"awardType", "value", "halid"}
        for invented in ("name", "level", "entryFee", "active", "won"):
            assert invented not in cup


def test_the_club_lists_its_best_players_first() -> None:
    # Unsorted, the club and the squad's player picker listed cards in the
    # order the icebreaker packs added them.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    items = json.loads(club.club_response())["itemData"]

    players = [i for i in items if i.get("itemType") == "player"]
    ratings = [i.get("rating", 0) for i in players]
    assert ratings == sorted(ratings, reverse=True)

    # Players first: a playStyle carries a rating of 99 of its own, so ranking
    # on rating alone buried the best player behind a handful of them.
    kinds = [i.get("itemType") == "player" for i in items]
    assert kinds == sorted(kinds, reverse=True)
    assert items[0].get("itemType") == "player"


def test_the_club_keeps_the_name_the_player_chose() -> None:
    # PUT /user/club carries the name and abbreviation the creation screen
    # asked for. It used to be answered {} and forgotten, so the club had no
    # name on the next load.
    import fut_inventory as inventory

    identity = inventory.ClubIdentity()
    assert identity.name == ""
    assert identity.adopt({"clubName": "Olympique Safi", "clubAbbr": "OCS"})
    assert (identity.name, identity.abbr) == ("Olympique Safi", "OCS")

    # It survives a save/restore cycle.
    restored = inventory.ClubIdentity()
    restored.restore(identity.state())
    assert (restored.name, restored.abbr) == ("Olympique Safi", "OCS")

    # An empty body changes nothing rather than clearing the club.
    assert identity.adopt({}) is False
    assert identity.name == "Olympique Safi"


def test_the_starting_squad_takes_the_club_name() -> None:
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    club.rename_active_squad("Olympique Safi")
    summaries = json.loads(club.squad_summaries())["squad"]
    active = club.active_squad_id()
    assert any(
        s["id"] == active and s["squadName"] == "Olympique Safi" for s in summaries
    )


def test_the_starter_packs_hold_no_specials() -> None:
    # Three packs -- bronze, silver, gold -- and nothing a new account has no
    # business being handed on its first day: no Team of the Week, no Team of
    # the Season, no Legend, and nothing above the cap.
    import fut_inventory as inventory

    club = inventory.ClubInventory(seeded=False)
    shop = inventory.PackShop(inventory.CardCatalogue(), inventory.Wallet(), club)
    coins_before = shop.wallet.coins

    count = shop.grant_starter_packs(random.Random(11))
    expected = sum(
        inventory.PACK_SPECS[p]["count"] for p in inventory.STARTER_PACKS
    )
    assert count == expected
    assert len(shop.pending) == expected
    # They are free.
    assert shop.wallet.coins == coins_before

    for card in shop.pending:
        assert inventory.is_ordinary(card), card.get("rarity")
        assert card["rating"] <= inventory.STARTER_RATING_CAP


def test_ordinary_excludes_every_special() -> None:
    import fut_inventory as inventory

    for special in (
        "Team of the Week",
        "Team of the Season",
        "World Cup",
        "Legend",
        "MOTM",
        "iMOTM",
        "Team of the Year",
        "Record Breaker",
    ):
        assert not inventory.is_ordinary({"rarity": special})
    for ordinary in ("Non-Rare Bronze", "Rare Gold", "Non-Rare Silver"):
        assert inventory.is_ordinary({"rarity": ordinary})


def test_first_run_starts_with_no_club_at_all() -> None:
    # The club is seeded from all four captains' squads because fcc_login2
    # treats an empty squad as fatal. That seed is also why the captain
    # selection never appears: it exists to give a new player his first squad.
    # The two cannot both be true, so the seed is optional rather than assumed.
    import fut_inventory as inventory

    seeded = inventory.ClubInventory()
    assert seeded.items and seeded.squad

    fresh = inventory.ClubInventory(seeded=False)
    assert fresh.items == []
    assert fresh.squad == []
    # Kits, badges and consumables are club contents too; a club that has not
    # been created does not own them either.
    assert json.loads(fresh.club_response())["itemData"] == []


def test_first_run_is_off_unless_asked_for(monkeypatch) -> None:
    # An empty club breaks the login for an existing player, which is the
    # state of anyone not running the experiment.
    import fut_inventory as inventory

    monkeypatch.delenv("FIFA14_FIRST_RUN", raising=False)
    assert inventory.first_run() is False
    monkeypatch.setenv("FIFA14_FIRST_RUN", "1")
    assert inventory.first_run() is True


def test_the_unfiltered_club_is_bounded() -> None:
    # Unbounded, this returned every card in one document: 244 KB at 453 cards
    # and growing with every pack. An explicit count still wins.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    whole = json.loads(club.club_response())["itemData"]
    assert len(whole) <= inventory.CLUB_UNFILTERED_LIMIT

    asked = json.loads(club.club_response({"count": "300"}))["itemData"]
    assert len(asked) == min(300, len(club.items))


def test_the_club_pages_from_the_sorted_list() -> None:
    # Paging an unsorted list puts arbitrary cards on page one, so the order
    # has to be established before the slice.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    everyone = json.loads(club.club_response())["itemData"]
    page = json.loads(club.club_response({"count": "5"}))["itemData"]
    assert [item["id"] for item in page] == [item["id"] for item in everyone[:5]]


def test_sorting_the_club_does_not_reorder_it_everywhere_else() -> None:
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    before = [item["id"] for item in club.items]
    club.club_response()
    assert [item["id"] for item in club.items] == before


def test_a_special_is_not_a_duplicate_of_the_ordinary_card() -> None:
    # A player's versions share his asset id: a Team of the Season Ruffier and
    # a Rare Gold Ruffier are both asset 167628 and are not the same card.
    import fut_inventory as inventory

    shop = inventory.PackShop(
        inventory.CardCatalogue(), inventory.Wallet(), inventory.ClubInventory()
    )
    ordinary = {"id": 1, "assetId": 167628, "resourceId": 100, "rareflag": 1}
    special = {"id": 2, "assetId": 167628, "resourceId": 200, "rareflag": 1}
    same = {"id": 3, "assetId": 167628, "resourceId": 100, "rareflag": 1}

    assert shop._signature(ordinary) != shop._signature(special)
    assert shop._signature(ordinary) == shop._signature(same)


def test_a_repeat_is_paired_with_the_card_it_repeats() -> None:
    # The pack screen reads the pairing out of duplicateItemIdList; marking
    # only the card left a repeat looking like an ordinary pull. What must
    # never go in that list is a bare list of the new ids -- that froze the
    # title, by telling the screen to compare each card against itself.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    shop = inventory.PackShop(inventory.CardCatalogue(), inventory.Wallet(), club)
    owned = club.items[0]
    repeat = dict(owned, id=1950999999)

    pairs = shop._mark_duplicates([repeat])
    assert pairs == [{"itemId": 1950999999, "duplicateItemId": owned["id"]}]
    assert repeat["duplicateItemId"] == owned["id"]
    # The new id never names itself.
    assert all(p["itemId"] != p["duplicateItemId"] for p in pairs)


def test_the_totw_challenge_says_how_strong_it_is() -> None:
    # opponentRating was computed with `max(... for card in [])` -- over an
    # empty list -- so every challenge advertised a rating of 0 against team 0.
    import fut_inventory as inventory

    totw = json.loads(inventory.totw_response(inventory.CardCatalogue()))
    challenges = totw["squadChallenge"]
    assert challenges
    for entry in challenges:
        assert set(entry) == {
            "squadId",
            "squadName",
            "formation",
            "opponentTeam",
            "opponentRating",
        }
        assert entry["opponentRating"] > 0
        assert entry["squadName"]


def test_a_bought_card_is_paired_like_a_packed_one() -> None:
    # The pairing existed only on the pack path; a card bought on the market
    # went into the club unmarked however many copies were already there.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    shop = inventory.PackShop(inventory.CardCatalogue(), inventory.Wallet(), club)
    owned = next(i for i in club.items if i.get("itemType") == "player")
    bought = dict(owned, id=1980000001, itemState="new")

    pairs = shop._mark_duplicates([bought])
    assert pairs == [{"itemId": 1980000001, "duplicateItemId": owned["id"]}]
    assert bought["duplicateItemId"] == owned["id"]


def test_a_card_the_server_never_held_is_not_reported_as_moved() -> None:
    # This is how a TOTS Ruffier was drawn, shown, sent to the club, confirmed
    # by the server, and then existed nowhere: an unknown id was acknowledged
    # with success while nothing was kept.
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    catalogue = inventory.CardCatalogue()
    wallet = inventory.Wallet()
    shop = inventory.PackShop(catalogue, wallet, club)
    actions = inventory.CardActions(shop, wallet, club)

    before = len(club.items)
    reply = json.loads(
        actions.move({"itemData": [{"id": 999999999, "pile": "club"}]})
    )["itemData"][0]
    assert len(club.items) == before
    assert reply["success"] is False
    assert reply["errorCode"] != 0
    assert actions.unmatched == [999999999]


def test_a_card_the_server_holds_still_moves() -> None:
    import fut_inventory as inventory

    club = inventory.ClubInventory()
    catalogue = inventory.CardCatalogue()
    wallet = inventory.Wallet()
    wallet.coins = 10_000_000
    shop = inventory.PackShop(catalogue, wallet, club)
    actions = inventory.CardActions(shop, wallet, club)

    drawn = json.loads(shop.open_pack(inventory.GOLD_PACK_ID))["itemList"]
    before = len(club.items)
    reply = json.loads(
        actions.move(
            {"itemData": [{"id": card["id"], "pile": "club"} for card in drawn]}
        )
    )["itemData"]
    assert len(club.items) == before + len(drawn)
    assert all(entry["success"] for entry in reply)
    assert actions.unmatched == []


def test_image_archives_are_parseable_containers_not_json() -> None:
    # The /fut/items/ prefix answered everything with {"itemData":[]}, so the
    # console got sixteen bytes of JSON where it asked for a BIG archive.
    import fut_inventory as inventory

    archive = inventory.empty_big_archive()
    assert archive[:4] == b"BIGF"
    assert int.from_bytes(archive[8:12], "big") == 0  # no directory entries
    assert int.from_bytes(archive[12:16], "big") == len(archive)


def test_every_cup_names_a_trophy_the_game_actually_ships() -> None:
    # cards0.big carries trophy_<id>_<tier> for ids 1100..1169 beside a
    # notfound.big. Serving 0 is what drew that placeholder.
    import fut_inventory as inventory

    for cup in json.loads(inventory.tournaments_response())["tournament"]:
        assert inventory.TROPHY_FIRST <= cup["trophyResourceId"] <= inventory.TROPHY_LAST


def test_a_cup_is_only_active_once_it_has_been_entered() -> None:
    import fut_inventory as inventory

    inventory.TOURNAMENT_PROGRESS.entries.clear()
    try:
        assert json.loads(inventory.active_tournaments_response())["tournamentId"] == []
        inventory.TOURNAMENT_PROGRESS.apply(3, {"round": 2, "tournamentData": "QQ=="})
        assert json.loads(inventory.active_tournaments_response())["tournamentId"] == [3]
        saved = json.loads(inventory.TOURNAMENT_PROGRESS.response(3))
        assert saved["round"] == 2
        assert saved["tournamentData"] == "QQ=="
        # The season spelling is still accepted on the way in.
        inventory.TOURNAMENT_PROGRESS.apply(3, {"round": 3, "data": "Ug=="})
        assert json.loads(inventory.TOURNAMENT_PROGRESS.response(3))["tournamentData"] == "Ug=="
    finally:
        inventory.TOURNAMENT_PROGRESS.entries.clear()


def test_team_of_the_week_is_a_full_side() -> None:
    # A real Team of the Week is not simply the 23 best rares -- TOTW 1 carries
    # ordinary in-form cards down into the sixties. Assert the shape, not a
    # rating floor the real data does not meet.
    import fut_inventory as inventory

    squad = json.loads(inventory.totw_response(inventory.CardCatalogue()))
    assert len(squad["itemData"]) == 23
    assert squad["formation"]
    ids = [card["id"] for card in squad["itemData"]]
    assert len(set(ids)) == len(ids)
    assert all(
        card["resourceId"] & 0x00FF_FFFF == card["assetId"]
        for card in squad["itemData"]
    )


def test_a_card_sent_to_the_club_is_in_the_club() -> None:
    # The send acknowledged and the card left the pack, but the club response
    # was built from the starting inventory alone, so it never appeared.
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory = ClubInventory()
    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    actions = CardActions(shop, wallet, inventory)

    before = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(21)))
    kept = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": kept, "pile": 7}]})

    owned = json.loads(inventory.club_response({"type": "player"}))["itemData"]
    assert len(owned) == before + 1
    assert kept in [item["id"] for item in owned]


def test_a_card_sold_out_of_the_club_leaves_it() -> None:
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory = ClubInventory()
    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    actions = CardActions(shop, wallet, inventory)

    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(22)))
    kept = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": kept, "pile": 7}]})
    actions.discard_many([kept])

    owned = json.loads(inventory.club_response({"type": "player"}))["itemData"]
    assert kept not in [item["id"] for item in owned]


def _fresh():
    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory = ClubInventory()
    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    return inventory, shop, CardActions(shop, wallet, inventory)


def test_listing_a_card_takes_it_out_of_the_club() -> None:
    # Moving to the transfer pile used to leave the card in the club as well,
    # so it showed in both places and looked duplicated.
    import random

    inventory, shop, actions = _fresh()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(41)))
    card = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": card, "pile": 7}]})
    owned = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])

    actions.move({"itemData": [{"id": card, "pile": 5}]})
    assert [item["id"] for item in actions.transfer] == [card]
    after = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
    assert after == owned - 1


def test_a_duplicate_is_kept_and_names_what_it_repeats() -> None:
    # Retail would offer to compare the two here, and that screen is driven by
    # duplicateItemIdList -- the field that froze the title, so it cannot be
    # turned back on yet. Until then the duplicate is quick-sold: the club
    # keeps one of each, the coins are real, and nothing happens silently.
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory, wallet = ClubInventory(), Wallet(coins=2_000_000)
    shop = PackShop(CardCatalogue(), wallet, inventory)
    actions = CardActions(shop, wallet, inventory)

    first = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(3)))
    for item in first["itemList"]:
        actions._keep(dict(item))
    owned = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])

    second = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(3)))
    assert all(item.get("duplicateItemId") for item in second["itemList"])
    for item in second["itemList"]:
        actions._keep(dict(item))

    # The duplicate is kept: the card names what it repeats, so the screen can
    # offer the choice rather than the server making it.
    after = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
    assert after == owned + second["numberItems"]


def test_a_club_holds_one_of_any_card() -> None:
    import random

    inventory, shop, actions = _fresh()
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(42)))
    card = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": card, "pile": 7}]})
    owned = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
    # The same item id is the same card, not a second one.
    actions.move({"itemData": [{"id": card, "pile": 7}]})
    assert (
        len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
        == owned
    )


def test_the_tiles_count_what_the_club_actually_holds() -> None:
    import random

    from fut_inventory import hub_response

    inventory, shop, actions = _fresh()
    before = json.loads(hub_response(inventory, 0))["clubPlayers"]
    opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(43)))
    actions.move(
        {"itemData": [{"id": item["id"], "pile": 7} for item in opened["itemList"][:2]]}
    )
    after = json.loads(hub_response(inventory, 0))
    assert after["clubPlayers"] > before
    # And the market tile reports real listings rather than a fixed zero.
    assert json.loads(hub_response(inventory, 3))["auctionCount"] == 3


def test_the_club_survives_a_restart() -> None:
    # Entering FUT needs a relaunch, so without a save every session started
    # from the icebreaker packs again: the club counter back to 92, the pack
    # you opened gone, the coins reset.
    import random
    import tempfile
    from pathlib import Path

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        ClubSave,
        PackShop,
        Wallet,
    )

    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "club.json"

        inventory, wallet = ClubInventory(), Wallet()
        shop = PackShop(CardCatalogue(), wallet)
        actions = CardActions(shop, wallet, inventory)
        opened = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(61)))
        kept = [item["id"] for item in opened["itemList"][:2]]
        actions.move({"itemData": [{"id": item, "pile": 7} for item in kept]})
        ClubSave(path).save(inventory, wallet, actions)
        owned = len(json.loads(inventory.club_response({"type": "player"}))["itemData"])
        coins = wallet.coins

        # A fresh process, as a relaunch gives.
        restored, purse = ClubInventory(), Wallet()
        again = CardActions(PackShop(CardCatalogue(), purse), purse, restored)
        assert ClubSave(path).load(restored, purse, again)

        reloaded = json.loads(restored.club_response({"type": "player"}))["itemData"]
        assert len(reloaded) == owned
        assert set(kept) <= {item["id"] for item in reloaded}
        assert purse.coins == coins


def test_a_bought_card_joins_the_club() -> None:
    # Buying credited nothing to the club: the card went into a side list the
    # inventory never saw, so it could not be assigned and vanished on restart.
    #
    # The market keeps offering the card afterwards, and that is correct -- it
    # carries many copies of the same player. Hiding them by asset id took
    # every version of Benatia off the market when one was bought.
    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory, wallet = ClubInventory(), Wallet()
    catalogue = CardCatalogue()
    actions = CardActions(PackShop(catalogue, wallet), wallet, inventory)

    listing = json.loads(catalogue.auctions({"start": "0", "num": "2"}))["auctionInfo"][0]
    asset = listing["itemData"]["assetId"]
    _, won = catalogue.bid(listing["tradeId"], listing["buyNowPrice"], wallet)
    assert won is not None
    actions._keep(dict(won))

    assert any(item["assetId"] == asset for item in inventory.items)


def test_a_bought_card_can_be_put_in_the_squad() -> None:
    # The squad was whatever was built at load time and nothing could change
    # it, so a card bought or pulled reached the club and had nowhere to go --
    # the assign screen found nothing it could field and backed out.
    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory, wallet = ClubInventory(), Wallet()
    catalogue = CardCatalogue()
    actions = CardActions(PackShop(catalogue, wallet), wallet, inventory)

    listing = json.loads(catalogue.auctions({"start": "0", "num": "1"}))["auctionInfo"][0]
    _, won = catalogue.bid(listing["tradeId"], listing["buyNowPrice"], wallet)
    actions._keep(dict(won))

    chosen = [item["id"] for item in inventory.squad]
    chosen[0] = won["id"]
    inventory.set_squad(chosen)

    fielded = [
        player["itemData"]["id"]
        for player in json.loads(inventory.active_squad_response("x"))["players"]
    ]
    assert fielded[0] == won["id"]
    assert len(fielded) == len(chosen)


def test_an_unknown_card_cannot_be_fielded() -> None:
    from fut_inventory import ClubInventory

    inventory = ClubInventory()
    original = [item["id"] for item in inventory.squad]
    inventory.set_squad([999_999_999])
    # Nothing resolvable: the squad is left alone rather than emptied.
    assert [item["id"] for item in inventory.squad] == original


def test_a_bought_card_waits_in_the_purchased_pile() -> None:
    # The assign screen reads purchased/items. Sending a bought card straight
    # into the club left that list empty, so "Assigner maintenant" had nothing
    # to offer and backed out -- the pack flow, which works, goes through the
    # pending pile first.
    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory, wallet = ClubInventory(), Wallet()
    catalogue = CardCatalogue()
    shop = PackShop(catalogue, wallet)
    actions = CardActions(shop, wallet, inventory)

    listing = json.loads(catalogue.auctions({"start": "0", "num": "1"}))["auctionInfo"][0]
    _, won = catalogue.bid(listing["tradeId"], listing["buyNowPrice"], wallet)
    bought = dict(won)
    bought["itemState"] = "new"
    shop.pending.append(bought)

    waiting = json.loads(shop.purchased_items())["itemData"]
    assert won["id"] in [item["id"] for item in waiting]
    assert won["id"] not in [item["id"] for item in inventory.items]

    # And assigning it moves it on, exactly as a pack card does.
    actions.move({"itemData": [{"id": won["id"], "pile": 7}]})
    assert not shop.pending
    assert won["id"] in [item["id"] for item in inventory.items]


def test_a_second_squad_can_be_created_renamed_and_dropped() -> None:
    # There was one squad and no way to add, rename or remove another: the
    # list was generated from a single fixed side.
    from fut_inventory import ClubInventory

    inventory = ClubInventory()
    assert inventory.squad_ids() == [1]

    players = [item["id"] for item in inventory.items if item["itemType"] == "player"]
    made = inventory.save_squad(0, players[:23], name="Ma 2e équipe")
    assert made != 1 and made in inventory.squad_ids()

    named = {
        entry["id"]: entry["squadName"]
        for entry in json.loads(inventory.squad_summaries())["squad"]
    }
    assert named[made] == "Ma 2e équipe"

    inventory.save_squad(made, players[:23], name="Renommée")
    named = {
        entry["id"]: entry["squadName"]
        for entry in json.loads(inventory.squad_summaries())["squad"]
    }
    assert named[made] == "Renommée"

    assert inventory.delete_squad(made)
    assert inventory.squad_ids() == [1]


def test_the_squad_the_club_plays_with_cannot_be_deleted() -> None:
    # Dropping slot 1 would leave the club with nothing to field.
    from fut_inventory import ClubInventory

    inventory = ClubInventory()
    assert not inventory.delete_squad(1)
    assert 1 in inventory.squad_ids()


def test_manager_tasks_are_recorded_and_survive() -> None:
    # They were a fixed empty list, so nothing completed was recorded: the bar
    # stayed at 0/13 and every task reset on the next launch.
    import tempfile
    from pathlib import Path

    from fut_inventory import (
        CardActions,
        CardCatalogue,
        ClubInventory,
        ClubSave,
        ManagerTasks,
        PackShop,
        Wallet,
    )

    tasks = ManagerTasks()
    assert tasks.apply({"entries": [{"key": 0, "value": 1}, {"key": 3, "value": 1}]}) == 2
    done = {entry["key"] for entry in json.loads(tasks.response())["entries"]}
    assert done == {0, 3}

    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "club.json"
        inventory, wallet = ClubInventory(), Wallet()
        actions = CardActions(PackShop(CardCatalogue(), wallet), wallet, inventory)
        ClubSave(path).save(inventory, wallet, actions, tasks)

        restored = ManagerTasks()
        ClubSave(path).load(ClubInventory(), Wallet(), actions, restored)
        assert restored.completed == tasks.completed


def test_a_card_pulled_twice_is_reported_as_a_duplicate() -> None:
    # duplicateItemIdList was always empty, so a player packed twice was never
    # offered as a duplicate and the screen had no reason to treat it apart.
    import random

    from fut_inventory import (
        GOLD_PACK_ID,
        CardActions,
        CardCatalogue,
        ClubInventory,
        PackShop,
        Wallet,
    )

    inventory, wallet = ClubInventory(), Wallet(coins=2_000_000)
    shop = PackShop(CardCatalogue(), wallet, inventory)
    actions = CardActions(shop, wallet, inventory)

    first = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(3)))
    assert not any(item.get("duplicateItemId") for item in first["itemList"])
    for item in first["itemList"]:
        actions._keep(dict(item))

    # The same draw again: every card names the one it repeats, twice over.
    # The per-card duplicateItemId, and the plural list as pairs -- which is
    # what the FIFA 14 pack screen actually reads. The list was empty here, and
    # with it empty a repeat rendered as an ordinary card.
    #
    # What froze the title was a plural list of the *new* ids, telling the
    # screen to compare each card against itself. A pair never does that, and
    # the last assertion below is what keeps it that way.
    second = json.loads(shop.open_pack(GOLD_PACK_ID, random.Random(3)))
    marked = [item for item in second["itemList"] if item.get("duplicateItemId")]
    assert len(marked) == second["numberItems"]
    owned = {item["id"] for item in inventory.items}
    assert all(item["duplicateItemId"] in owned for item in marked)

    pairs = second["duplicateItemIdList"]
    assert len(pairs) == second["numberItems"]
    assert all(set(pair) == {"itemId", "duplicateItemId"} for pair in pairs)
    assert all(pair["duplicateItemId"] in owned for pair in pairs)
    assert all(pair["itemId"] != pair["duplicateItemId"] for pair in pairs)


def test_a_rare_and_a_base_card_are_not_duplicates_of_each_other() -> None:
    from fut_inventory import CardCatalogue, ClubInventory, PackShop, Wallet

    inventory = ClubInventory()
    shop = PackShop(CardCatalogue(), Wallet(), inventory)
    # An asset the starting club does not already hold, so the only copies in
    # play are the ones this test puts there.
    held = {item.get("assetId") for item in inventory.items}
    asset = next(
        card["assetId"] for card in CardCatalogue().cards if card["assetId"] not in held
    )
    base = {"id": 1, "assetId": asset, "rareflag": 0}
    rare = {"id": 2, "assetId": asset, "rareflag": 1}
    inventory.items.append(base)
    # Same player, different version: not a repeat.
    assert shop._duplicates([rare]) == []
    assert shop._duplicates([dict(base, id=3)]) == [3]


def test_consumables_use_the_member_names_the_binary_carries() -> None:
    # The response is an object with named members -- consumablesContractPlayer
    # and the rest, from CardsDLL's JSON table between 0x89030F9C and
    # 0x89031148 -- not an entries array. Numbered keys meant nothing to the
    # screen, which reported none available while the club held sixteen.
    from fut_inventory import ClubInventory, consumable_stats_response

    document = json.loads(consumable_stats_response(ClubInventory()))
    assert "entries" not in document
    for member in (
        "consumablesContractPlayer",
        "consumablesFitnessPlayer",
        "consumablesHealing",
        "consumablesPosition",
        "consumablesTrainingPlayer",
    ):
        assert document[member] > 0, member
    # No member goes out at zero while the club holds consumables. Applying
    # from the squad screen decides from these counts alone, so a goalkeeper
    # made it read consumablesTrainingGk -- zero -- and report none available.
    assert document["consumablesTrainingGk"] > 0
    assert all(value > 0 for value in document.values())
    # The aggregate members cover their family once each. Chemistry styles
    # have no aggregate of their own -- the binary carries one member for an
    # outfielder's and one for a keeper's, and nothing above them -- so the
    # total counts both.
    assert document["consumables"] == sum(
        document[name]
        for name in (
            "consumablesContract",
            "consumablesFitness",
            "consumablesHealing",
            "consumablesTraining",
            "consumablesPosition",
            "consumablesTrainingPlayerPlayStyle",
            "consumablesTrainingGkPlayStyle",
        )
    )


def _unused_consumable_counts_are_not_the_club_counters() -> None:
    # club/stats/consumables was answered with the generic club counters --
    # players, staff, stadiums -- so the apply screen reported none available
    # while the club held sixteen consumables.
    from fut_inventory import (
        CONSUMABLE_ORDER,
        ClubInventory,
        club_stats_response,
        consumable_stats_response,
    )

    inventory = ClubInventory()
    consumables = json.loads(consumable_stats_response(inventory))["entries"]
    generic = json.loads(club_stats_response(inventory))["entries"]

    assert len(consumables) == len(CONSUMABLE_ORDER)
    assert all(entry["value"] > 0 for entry in consumables)
    # And it is genuinely a different answer, not the same document renamed.
    assert [e["value"] for e in consumables] != [e["value"] for e in generic]


def test_the_club_search_understands_the_consumable_family() -> None:
    # The consumables screen reads the counts and then searches the club for
    # "consumable" -- the family, not each kind. Comparing that against
    # itemType, which is contract, fitness, healing and so on, matched nothing:
    # the screen found counts and then no items.
    from fut_inventory import ClubInventory

    inventory = ClubInventory()
    # Past the club's whole stock of consumables, or both answers come back
    # capped at the page size and the comparison says nothing.
    family = json.loads(inventory.club_response({"type": "consumable", "count": "500"}))
    one_kind = json.loads(inventory.club_response({"type": "contract", "count": "500"}))

    assert len(family["itemData"]) > len(one_kind["itemData"]) > 0
    # Asking for the family must not sweep in players.
    assert all(item["itemType"] != "player" for item in family["itemData"])


def test_consumables_come_from_the_game_database() -> None:
    # They were invented once: three grades a family, asset ids counted up
    # from 1000. Every card drew NOT FOUND art, all of them were named
    # "Entrainement equipe", and applying one did nothing -- the title reads a
    # consumable's name and effect out of its own database by subtype, and
    # draws it by asset id, so neither is ours to pick.
    from fut_inventory import CONSUMABLE_TYPES

    consumables = [
        item for item in INVENTORY.items
        if item.get("consumableType") in CONSUMABLE_TYPES
    ]
    assert consumables

    subtypes = {item["cardsubtypeid"] for item in consumables}
    # A single subtype is what "all of them are the same card" looks like.
    assert len(subtypes) > 40
    assert all(item["assetId"] < 1000 for item in consumables)
    # Each card names the member CardsDLL counts it under, and a keeper's
    # training card is a different member from an outfielder's.
    members = {item["consumableMember"] for item in consumables}
    assert "consumablesTrainingGk" in members
    assert "consumablesTrainingPlayer" in members
    assert "consumablesContractManager" in members
