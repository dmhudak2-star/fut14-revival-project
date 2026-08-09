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

## Manager and consumables: deliberately not modelled

Both were asked for, and both are being left out on purpose rather than
overlooked.

**Manager.** The PC revival serves `"manager":[]` on the active squad and
`"manager":[{"id":0}]` on its empty-squad shape, and its FUT works. So a
manager item is not required to field a side. Inventing one means inventing an
asset id, and an asset id with nothing behind it is exactly the failure mode
this file exists to avoid -- the record parses, the card draws blank, and the
squad screen may reject the whole response rather than the one item. Not worth
putting on the critical path to a first match.

**Consumables.** Every player in the catalogue ships with contract 99 and
fitness 99, no injury and no suspension. Consumables exist to restore those.
There is nothing to restore, so nothing to consume, and an empty consumables
list is the accurate answer rather than a placeholder.

Both become worth adding once a match has actually been played, when contracts
start decrementing and there is a real reason for the item to exist.

## The consumables are placeholders, and the screen shows it

Screenshot: `runtime/screens/conso-143655.png`

The club search lists them, so the plumbing works -- counts, family filter,
paging. But every card draws **NOT FOUND**, every one is labelled *Entraînement
équipe* whatever kind it is meant to be, and every effect reads **+0**.

The cause is the one this file already states for players, applied to
consumables and missed: the title resolves a card's name, art and effect from
its `assetId`. The consumables here were given invented ids -- 1000, 1001,
1002 -- which match nothing on the disc. So the art is missing, the type falls
back to a default, and the bonus is nothing.

The players work because their ids are real, taken from the icebreaker packs
and the wefut catalogue. There is no equivalent source for consumables: the
wefut catalogue holds players only, and `cards_ng_db.db` -- which would carry
them -- still does not decode.

So consumables cannot be made to work by adjusting the responses. Until real
consumable asset ids are found, the counts and the search will keep listing
cards the title cannot draw, and applying one will keep doing nothing, because
there is nothing behind it to apply.
