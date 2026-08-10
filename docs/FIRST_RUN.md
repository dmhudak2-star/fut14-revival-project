# The first-time journey, and what was hiding it

`FIFA14_FIRST_RUN=1` makes the server describe a player who has never played.
Off by default.

The project's stated goal since its first handoff is the real first FUT
journey: loader, updates, intro video, **the four captains**, and a club
actually created. Sessions reached FUT, the club, the market and the cups —
but never that. This records why.

## The server was answering two different questions two different ways

`accountinfo` said `personas: []`, `returningUser: false` — a brand-new
player. Everything else described an established one:

| Route | Said |
| --- | --- |
| `club` | 358 cards |
| `squad/list` | a squad named `Fondateur FUT` |
| `clubUser` | persona `Fondateur FUT` |
| `user` | `clubName: Fondateur FUT`, `clubNameChangeAllowed: false` |

Faced with that, the client believes the club. It has no reason to offer a
captain selection whose purpose is to give a player his first squad, and no
reason to ask for a club name that already exists — and was in any case
declared unchangeable.

## Why the club was seeded in the first place

`ClubInventory` loads **all four** captain squads from the icebreaker pack
list, which is where 358 cards come from. That was not an accident: `fcc_login2`
treats an empty squad as fatal, and seeding the club is what got past it.

So the seed that makes FUT work is the same seed that hides the journey. The
flag exists to stop guessing which side wins and let the console answer.

## What the console answered

Emptying the cards alone was **not enough**. With `club` at 0 items the title
still walked into the FUT hub and showed `MON CLUB — 0 NOMBRE TOTAL DE
JOUEURS`. No captain selection, and no login failure either.

That second half matters: `fcc_login2` did **not** refuse. The squad document
still carries 23 slots, each an empty `itemData: {id: 0}`, so the vector is not
empty even when the club is. The documented fatality is about an empty vector,
not an unfilled one.

Then the club name was found still being asserted in three more places, and
`clubNameChangeAllowed` was hardcoded `false`. Those are fixed under the same
flag. Whether that is enough has not yet been tested on the console.

## What the flag changes

    FIFA14_FIRST_RUN=1

* `ClubInventory` starts empty — no cards, no squad, and no kits, badges,
  stadiums or consumables either;
* every club name goes out empty — `squad/list`, `clubUser`, `user`;
* `clubNameChangeAllowed` becomes true while the club has no name.

Nothing else changes, and with the flag unset the server behaves exactly as
before.

## Also worth knowing

The pack list has only ever been requested once in this project's history, on
2026-08-07, and that run had a **client-side** route patch in `data1.big`
pointing `futLogIn1 / advance` at `iceBreaker`. See
`docs/ICEBREAKER_ROUTE_REPRODUCTION.md`. So the captain selector may not be
reachable from server state alone. The flag tests the server half of that
question; it does not settle the client half.
