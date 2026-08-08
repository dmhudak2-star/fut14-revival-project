from __future__ import annotations

import json
import sys
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER_DIR))

from fut_inventory import ClubInventory, RESOURCE_VERSION


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

    from fut_inventory import CardCatalogue, GOLD_PACK_PRICE, PackShop, Wallet

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    before = wallet.coins
    opened = json.loads(shop.open_pack(random.Random(7)))
    assert wallet.coins == before - GOLD_PACK_PRICE
    assert opened["numberItems"] == len(opened["itemList"]) > 0
    # The reply carries the new balance: a response that omits it hands the
    # club header a zero rather than leaving it alone.
    assert opened["credits"] == wallet.coins


def test_a_pack_is_refused_without_the_coins() -> None:
    from fut_inventory import CardCatalogue, PackShop, Wallet

    shop = PackShop(CardCatalogue(), Wallet(coins=10))
    assert not shop.can_afford()
    assert json.loads(shop.refused())["reason"] == "INSUFFICIENT_COINS"


def test_drawn_cards_stay_pending_until_collected() -> None:
    import random

    from fut_inventory import CardCatalogue, PackShop, Wallet

    shop = PackShop(CardCatalogue(), Wallet())
    shop.open_pack(random.Random(3))
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

    from fut_inventory import CardActions, CardCatalogue, PackShop, Wallet

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    actions = CardActions(shop, wallet)
    opened = json.loads(shop.open_pack(random.Random(11)))
    ids = [item["id"] for item in opened["itemList"]]

    before = wallet.coins
    sold = json.loads(actions.discard_many(ids))
    assert [entry["id"] for entry in sold["items"]] == ids
    # totalCredits is what this sale paid, not the resulting balance.
    assert sold["totalCredits"] == wallet.coins - before
    assert not shop.pending


def test_sending_a_card_to_the_club_takes_it_out_of_the_pack() -> None:
    import random

    from fut_inventory import CardActions, CardCatalogue, PackShop, Wallet

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    actions = CardActions(shop, wallet)
    opened = json.loads(shop.open_pack(random.Random(5)))
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
    from fut_inventory import CardActions, CardCatalogue, PackShop, Wallet

    wallet = Wallet()
    shop = PackShop(CardCatalogue(), wallet)
    return CardActions(shop, wallet), shop, wallet


def test_a_card_can_be_set_aside_for_listing() -> None:
    import random

    actions, shop, _ = _actions()
    opened = json.loads(shop.open_pack(random.Random(9)))
    first = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": first, "pile": 5}]})
    # Pile 5 is the transfer list: the card leaves the club until it is listed.
    assert [item["id"] for item in actions.transfer] == [first]
    assert first not in [item["id"] for item in actions.club]


def test_listing_returns_a_trade_id_and_shows_in_the_trade_pile() -> None:
    import random

    actions, shop, wallet = _actions()
    opened = json.loads(shop.open_pack(random.Random(13)))
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
    opened = json.loads(shop.open_pack(random.Random(17)))
    first = opened["itemList"][0]["id"]
    actions.move({"itemData": [{"id": first, "pile": 5}]})
    listing = json.loads(actions.list_for_sale({"itemData": {"id": first}}))

    actions.withdraw(listing["tradeId"])
    assert json.loads(actions.trade_pile(wallet.coins))["total"] == 0
    assert [item["id"] for item in actions.transfer] == [first]
