# Handoff, night of 2026-08-08

Supersedes `HANDOFF_2026-08-08.md`. Working copy
`~/Downloads/fifa14-fut-stable/fifa14-fut-offline-revival`, branch `main`,
229 tests pass. Console `192.168.1.25`, Mac/server `192.168.1.36`.

## FUT is entered. This is no longer the blocker.

Screenshots in `runtime/screens/`:

| screen | file |
|---|---|
| club creation | `hub-023946.png` |
| club name confirm | `club-confirm-024102.png` |
| manager-task hub | `cycle-club-024754.png` |
| FUT main menu | `futmenu-024850.png` |
| Play menu | `tab2-025022.png` |

## The six changes that got there, in order

1. **Remove the probe at `0x8910AAF8`.** On the tail every notification id
   shares; it suppressed `/ut/auth` outright. Never hook that address.
2. **Trusted device** answers `{"trusted":true,"changed":false,"exists":true,"locked":false}`.
   Removes the security question, and the account-state drift behind it.
3. **`accountinfo`** serves an empty persona list.
4. **`/tutorials` answers 404.** This was the two-day wall. CardsDLL pairs
   `RetrieveShouldShowTutorial` with `RetrieveShouldShowTutorialComplete`, so
   the retrieval is asynchronous and `DoInitialLoginSteps` waits on it. The
   invented empty `<MESSAGES>` document was accepted as HTTP and never
   completed as a document. `FUT/DISABLE_TUTORIALS` and `FUT/FORCE_TUTORIALS`
   do **not** gate the request -- the client makes it either way.
5. **`squad/list` and `squad/active`** serve a real squad. An empty vector is
   fatal to `fcc_login2`.
6. **`PUT user/club`** answers `{}`. The club rename; a 404 reads as a
   connection error. Not in the PC reference -- the PC client renames through
   `clubUser`.

Plus `season/list` and `season/user`, which Saison Joueur Solo needs.

## The club

`server/fut_inventory.py`, documented in `docs/FUT_CLUB_INVENTORY.md`. 97 items
built from `server/icebreakerpacklist.json` -- this build's own icebreaker
packs, so the asset ids are real (158023 Messi, 167397 Neymar). 92 players plus
home kit, away kit, badge, stadium, ball. Starting eleven rated 87. Credits
50000, pile capacity 20000, a buyable 7500-coin gold pack, and the full market
response shape.

Manager and consumables are deliberately absent; the reasoning is in that file.

## Where it stands, unvarnished

**Not yet verified in game:** that the title accepts this inventory. No
screenshot yet of eleven players on the squad screen. Everything about the
inventory is server-side confirmation only.

**Current blocker, and it is environmental.** After a cold relaunch the title
loops through its intro clips and the title screen does not act on START.
Virtual presses are delivered -- `pulse` writes the mailbox and the frame
counter drains -- but nothing advances, which is what an unsigned Xbox profile
looks like. No software hook here can sign one in. A single physical START
press on the title screen clears it. The user has been mailed.

Two things that wasted time and should not waste it again:

* the virtual pad keeps its last `buttons` value after `remaining_frames`
  reaches zero. It is cosmetic -- the frame counter is what gates the override
  -- but `status` showing `buttons=0x0010` looks exactly like a stuck button.
* the screen navigator matches dark intro-video frames against the `fut_error`
  signature at distance 20-40, and reports a FUT error on a title that has only
  just booted. It says so itself: "fut_error did not respond to its own button".

## Next, once the title is at the main menu

1. Enter FUT, reach the squad screen, screenshot the eleven. That is the
   inventory verification.
2. Play -> Saison Joueur Solo, and follow whatever 404s appear. The FUT API
   entries still at zero are `GetUserStatsData`, `CreateMatch`,
   `ServiceQuickMatch`, `ServiceCreateSession`, `GetRandomOpponent`.
