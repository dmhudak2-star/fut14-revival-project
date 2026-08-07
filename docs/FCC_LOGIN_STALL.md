# Why `fcc_login` never finishes loading

The FUT bootstrap ends on a modal `Chargement…` dialog with no buttons. This
records what that dialog is, what it waits for, and what the evidence says is
missing — all of it read from the shipped assets, which `tools/archive/lzx_decode.py`
can now decompress, plus the passive traces already in this repository.

## The dialog is `fcc_login1`'s own loading popup

`futLogInFlow.nav` gives `futLogIn1` the screen
`external/ion_fut/screens/fcc_login1`. Decoding that screen's ActionScript
constant pool gives its whole shape:

```text
BeginLogin()
    gIndicators.ShowLoadingIcon
    gStdPopup.isPopupUpById(FUT_LOGIN_POPUP_ID)
    showByObj({strId, message:"Loading", nButtons:0})
    gCardsLoginHelper = external/ion_fut/classes/CardsLoginHelper
    Init(..., OnError, InitialLoginDone)
    DoInitialLoginSteps

InitialLoginDone(NEW_USER, CUSTOM_DATA_AVAILABLE, CLUB_NAME_CHANGE)
    CLUB_NAME_CHANGE -> ContineToChangeClubName -> nav "changeClubName"
    NEW_USER         -> ContinueToCreateClub
    otherwise        -> ContinueLogIn          -> nav ACTION_ADVANCE

ContinueToCreateClub()
    deletePopup
    FUT_IcebreakerManager.SkipIceBreaker
    ION_FantasyTeam.GetFUT1TeamName / IS_RETURN_USER
    UIFUtility.SendAction(ACTION_NAV, "iceBreaker" | "createClub")

OnError()
    ONL_SERVERS_DOWN / OSDKCards_ShowErrorPopup
```

`message:"Loading"` with `nButtons:0` is exactly the buttonless dialog on
screen. So the popup is not a rendering fault and not a missing asset: it is
the screen's own deliberate wait.

## What it is waiting for

`BeginLogin` hands control to `CardsLoginHelper`, an ION class that is not in
`cards0.big` or `data1.big` — it is native, inside `CardsDLLzf.xex.dll`. The
screen then does nothing at all until that helper calls back
`InitialLoginDone`. Every route out of `futLogIn1` is downstream of that one
callback.

The FUT API trace shows what the native side actually does:

```text
LoginToFUT       1 call
FirstTimeInit    1 call
GetIdentityData  never called
CardsDownloaded  never called
CreateClub       never called
```

So `DoInitialLoginSteps` starts, submits `FirstTimeInit`, and stops. No
completion arrives, so `InitialLoginDone` is never called, so the popup is
never deleted and no navigation event is ever sent.

That last part is confirmed independently: with a passive hook on
`SendNavEvent` armed across a whole session, the title emitted **zero**
navigation events of its own. Every record came from injection.

## Why forcing the navigation cannot work on its own

`iceBreaker` looked like a way past this, and `futLogIn1` really does declare
it — unlike the PC build, which had to be patched to add it. But the decoded
screen shows the choice between `iceBreaker` and `createClub` is made *inside*
`ContinueToCreateClub`, which only runs after `InitialLoginDone`. Dispatching
the event by hand skips the step that deletes the modal popup, and a screen
with a modal up does not act on the transition. Observed exactly that: the
event is accepted and nothing moves.

`advanceRequest` is the exception, and it is worth keeping: the title also
never sends that one, and injecting it does move the flow from
`futLauncher/launchFUTFlow` into `futFlow`. That is a real step forward, but
it only delivers the flow to this same wait.

## Where this points

The question is no longer which event to send. It is why `FirstTimeInit`'s
completion never reaches the login helper. Notably the last HTTP request of
the session is `/ut/game/fifa14/user/accountinfo`, answered, followed by
nothing but Blaze frames — so the helper is holding after that response
rather than asking for anything else.

The three flags `InitialLoginDone` carries — `NEW_USER`,
`CUSTOM_DATA_AVAILABLE`, `CLUB_NAME_CHANGE` — are account-shaped, which makes
the `accountinfo` response and whatever `FirstTimeInit` expects alongside it
the first thing to check.

## The PC popup patch, transposed and measured

The PC revival project removes this popup by turning the one-byte `EQUALS2`
at `fcc_login1`'s popup-presence compare into `OR`, so the branch that
normally skips popup construction always takes. This build's APT is
byte-identical to theirs at that offset, context bytes included, so the same
edit applies cleanly.

It was built, verified against libmspack as an outside decoder, deployed with
a byte-for-byte verified upload, and run:

* the popup is genuinely gone -- `fcc_login1` shows an empty screen where
  `Chargement…` used to be;
* but the login sequence stops **earlier** than it did unpatched. Retail
  reaches `phishing/validate` and then continues through
  `settings`, `match/reset`, `user`, `userdata`, `tutorials`; patched, nothing
  follows the validate at all.

So on this build the popup is not merely an overlay left behind: something on
that path is load-bearing. The patch is kept in the repository and reverted on
the console.
