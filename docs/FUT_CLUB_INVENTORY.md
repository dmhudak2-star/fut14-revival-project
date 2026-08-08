# The club inventory

A FUT match needs eleven real player items, and every screen before it needs a
club to look at. Empty fixtures do not work here: `fcc_login2` treats an empty
squad vector as fatal, and an empty club leaves nothing to field.

## Where the cards come from

Not invented. `data/db/cards_ng_db.db` inside `cards0.big` is the real card
database, and it still does not decode -- its chunks report an LZX block type
of 7 at the first symbol, which no window size fixes.

But `server/icebreakerpacklist.json` does decode, and it is this same build's
own data: four starter squads of twenty-three players, each carrying a real
asset id, rating, rare flag, club and six attributes. `158023` is Messi,
`167397` is Neymar. Those are cards this disc can actually draw, which an
invented asset id would not be.

`server/fut_inventory.py` unpacks those packs into the item shape the FUT
endpoints expect:

* 92 player items -- the first pack becomes the starting squad, the other three
  stay in the club as spares so the club and transfer screens have stock;
* 5 presentation items -- home kit, away kit, badge, stadium, ball, without
  which the club cannot present itself and neither side can be dressed.

97 items total, starting eleven rated 87.

## The invariants that matter

* `resourceId` carries the asset id in its low 24 bits with a version byte
  above it (`0x01000000` for a base card). Break this and the card art stops
  resolving -- the record still parses and the card draws blank.
* Item ids must be unique and must not collide with the presentation range
  (`1_700_000_00x`); players start at `1_600_000_001`.
* Every player ships with full contract and full fitness, no injury and no
  suspension. A card that cannot take the field is, for a first match, the
  same as no card.
* The eleven who start wear shirt numbers 1-11; the bench carries 0, as retail
  does.

`tests/test_fut_inventory.py` holds each of these.

## Routes it serves

| route | was | now |
|---|---|---|
| `squad/list` | `{"squad":[]}` | one summary, rated from the starting eleven |
| `squad/active` | `{"squad":[]}` | 23 players, kits, badge, stadium, ball |
| `club` | 404 | the whole 97-item catalogue |
| `purchased/items` | static | generated (still empty: nothing is pending) |

## Still open

The transfer market, the store packs and the consumables are not modelled yet.
`store/purchasegroup/all` still serves one deliberately invalid pack, so the
store screen constructs without offering anything buyable.
