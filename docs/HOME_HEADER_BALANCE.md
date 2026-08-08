# The FUT home header still reads zero

The club header shows `CRÉDITS 0` on the FUT home while the store, on the same
session, shows the real balance and pack purchases debit it correctly.

## What is established

* `/ut/game/fifa14/user` returns the flat FutGetUserInfo document with
  `coins`/`credits` set, and `/ut/game/fifa14/user/credits` returns the
  currencies contract with lower-case `coins`/`points`. Both were verified by
  querying the server directly.
* The store screen refetches credits and displays the right figure, so those
  two responses are being parsed.
* A quick sell writes the header correctly -- 50000 became 50200 on screen.
* Adding the balance to `hub`, `eventfeed` and `clubUser` changes nothing.

## What the bisection cost, and what it proved

Adding the balance to **every** FUT reply froze the login at
`clientdata/tutorialpopups`. Adding it to `clientdata/userHubData` alone froze
it there instead. Both parsers reject an object carrying members they do not
know, and the login step waiting on that response never completes.

So the earlier assumption -- that an unrecognised sibling member is always
skipped -- is false, and holds only for some of these responses. The three that
tolerate it are `hub`, `eventfeed` and `clubUser`; the list should not be
extended without watching where the fan-out stops.

## What is not established

Which response the home header actually reads. It is not any of the five tried,
and it is not simply "the last one carrying a balance", because the responses
that do carry one are parsed successfully elsewhere on the same session.

The remaining possibilities, in the order worth testing:

1. the header is populated from the Blaze session rather than from HTTP -- the
   FUT user session component, not the identity server;
2. it reads a response whose own schema names the balance differently, the way
   `FutUserCreditsServerResponse` needs lower-case currency names;
3. it is written once at FUT entry and simply never refreshed at home, in which
   case the ordering of the login fan-out matters rather than its content.

This is cosmetic for play: the store shows the true balance, packs cost coins,
quick sells pay, and bids are refused when the wallet cannot cover them.
