# Which event fcc_login1 sends, and why it was never iceBreaker

The captain selection never appears. This is where the decision is taken,
read out of the game's own files rather than guessed.

## The flow graph already has the transition

`data/ui/nav/fut/futloginflow.nav`, decompressed out of `data1.big`:

```text
futLogIn1  --advance-->        futLogIn2
           --createClub-->     createClub
           --iceBreaker-->     iceBreaker
           --changeClubName--> changeClubName

iceBreaker   external: fut/futIcebreakerFlow.nav
             outputs: advance --> createClub
```

So the intended journey for a new player is
`futLogIn1 → iceBreaker → createClub`, and the run of 2026-08-10 went
`futLogIn1 → createClub`. The event was sent; the wrong one.

## The screen decides, and these are its inputs

`fcc_login1` lives in `cards0.big` at entry 2385 -- a `chunklzx` container
holding a 3548-byte BIGF with two APT parts. Its constant pool names the whole
decision:

```text
fcc_login::InitialLoginDone()
    NEW_USER · CUSTOM_DATA_AVAILABLE · CLUB_NAME_CHANGE
    -> ContineToChangeClubName | ContinueToCreateClub | ContinueLogIn

fcc_login::ContinueToCreateClub()
    FUT_IcebreakerManager.SkipIceBreaker
    ION_FantasyTeam.GetFUT1TeamName
    IS_RETURN_USER
    -> createClub          (sends ACTION_NAV createClub)
    -> iceBreaker          (after FUT_ICEBREAKER_CRITICAL_SECTION,
                            ShowPOWOverlay, BeginNetworkOperation)
```

`ContinueToCreateClub` was reached, so `NEW_USER` was true. The branch inside
it chose `createClub`.

## SkipIceBreaker is native, and it is not our config

`SkipIceBreaker` and `GetFUT1TeamName` are both in CardsDLL, in the icebreaker
manager's binding block:

```text
HasUserDoneIB · GetRandomClubName · UpdateCaptainSelection
CheckCharityMatchDataAvailability · GetCharityMatchTeamKits
RetrieveUserActions · BuildSquad · SkipIceBreaker
RetrievePack · ClearSquad · RetrievePackList
```

`FUT_SKIP_ICEBREAKER_FLOW`, which `OSDK_CORE` serves as `"0"`, appears nowhere
in CardsDLL. So that key is not what `SkipIceBreaker` reads, and setting it has
never been able to matter.

`HasUserDoneIB` and `RetrieveUserActions` sit together in that block, and
`RetrieveUserActions` is `GET /ut/game/fifa14/user/action`.

## What was fixed, and what it rests on

That route answered `{"userActionList":[]}`. **`userActionList` is in no
member-name table.** `actions` is -- directly beside `actionType` in the sorted
run at 201200. So the list the icebreaker manager asks for was served under a
name its parser cannot see, and a list that cannot be read is not an empty
list: it is no list at all.

Both spellings now go out. Whether that alone makes `HasUserDoneIB` answer
false has **not** been tested on the console.

## If it is not enough

The remaining lever is `SkipIceBreaker` itself, which needs its code read.
`tools/fifa14_get_card_duplicate_locate.py` finds a named binding in the mapped
module and can be pointed at `SkipIceBreaker` instead; CardsDLL has to be
mapped, which means being inside FUT.

The fallback is the client-side route patch,
`tools/archive/build_fifa14_icebreaker_route_patch.py`, which retargets
`futLogIn1 / advance` at `iceBreaker`. It is the only configuration in this
project's history that ever made the console ask for the pack list, on
2026-08-07 -- and it failed then because the FUT login never completed in those
sessions. That is no longer true: the login now runs and a club can be created.
So the reason it failed has gone, but it rewrites `data1.big` on the console
and should be backed up first.
