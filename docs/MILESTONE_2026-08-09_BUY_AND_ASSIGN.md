# Milestone: buy a player and put him in the club

Screenshots:
* `runtime/screens/ronaldo-000958.png` -- the purchase confirmed
* `runtime/screens/asg2-004222.png` -- the assign screen with the card on it
* `runtime/screens/asg3-004307.png` -- back to the market, card gone

The full loop works: search, buy, confirm, assign, keep -- and it survives a
relaunch.

```text
NOUV. ÉLÉMENTS 1 | ÉQUIPE ACTIVE 23/23 | LISTE TRANSFERTS 0/20000
A Choisir   X Envoyer au club   Y Placer sur la Liste des transferts
```

Cristiano Ronaldo, 98, bought for 20 200 and sent to the club. The journal:
`fut_item_move -> club: 37, pending: 0`.

## Three faults, one after the other

**`EXPIRE_TIME`.** The actions panel opened empty and "Temps restant" read
`--`. Four expiry shapes had been tried, all reasoned by analogy with the FUT
web app, two of which emptied the detail panel outright. The member CardsDLL
reads is `EXPIRE_TIME`, sitting directly beside `FUT_AUCTION_EXPIRED` -- the
localisation key of the message on screen. With it, the timer filled in and the
panel offered Faire offre, Acheter maintenant, Offre échange.

**`/offer`, not `/bid`.** Buying then failed with "cette liste a expiré" while
the timer read 23 h 59. The journal named the real cause in one line: `POST
/ut/game/fifa14/trade/{id}/offer`, unhandled.

**`purchased/items`.** The purchase went through, the coins were taken, and
"Assigner maintenant" backed out to the search screen without ever asking the
server to save anything. The trace showed the client reading `squad/active` and
`purchased/items` -- and that pile was empty, because the bid handler put the
card straight into the club. The pack flow, which has worked all along, goes
through the pending pile first. Bought cards now take the same route.

## What this keeps confirming

An error message on this title names a plausible cause, not the real one:

* the loading popup was not a broken login -- it was the tutorial feed;
* the expired listing was not expired -- the timer read 23 hours;
* "Assigner maintenant" reported nothing at all, and the fault was two screens
  away in a list nobody had thought to fill.

Every fix that landed first time came from the binary's strings or the journal.
Every guess reasoned from the web app cost a relaunch and produced nothing.
