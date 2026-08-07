# Where the FUT login actually stalls

Traced from the service object down to the script, 2026-08-08.

## The native API is a thin shell over one service

`LoginToFUT` at `0x89105D18` is an adapter: it dereferences the singleton at
`*0x892213A0` (= `0xB5A9F018`, vtable `0x89008E90`) and calls `vtable[1]`
(`0x8908D350`). Every other FUT API entry has the same shape. The state lives
in the service, not in the entry points.

`0x8908D350` publishes `"main"` as false, then calls a manager with three
strings:

```text
r4 = "_global"   r5 = "LoginToFUT"   r6 = ""   r7 = 0
```

with `"SUCCESS"` and `"FAILED"` sitting beside them in the same string blob.
So the native side is bound to a script symbol, `_global.LoginToFUT`.

The whole `ION_FCC` name table is at `0x89016DD0`: `GetIdentityData`,
`CreateClub`, `CardsDownloaded`, `GetUserStatsData`, `FirstTimeInit`,
`CreateMatch`, `ServiceQuickMatch`, `GetRandomOpponent`, `FinalShutdown` and
the rest -- every name this project traces. Neither `CardsLoginHelper` nor
`DoInitialLoginSteps` is in it, and neither string appears anywhere in
`0x89000000..0x89030000`.

## futLogIn0 is not the problem: it completes

`data/ui/external/ion_fut/screens/fcc_login.big` decodes, and its constant
pool gives the first state's whole shape:

```text
BeginLogin()        ShowLoadingIcon; popup {message:"Loading", nButtons:0}
                    setInterval(Check_AdapterInit)
Check_AdapterInit() ION_FCC.LoginToFUT   (polled)
                    clearInterval(mCheckAdapterInitInterval)
Handle_LoginToFUT() _global ... -> Login()
Login()             overrideFifaScreenIds
                    gScreenFlowManager.setFlowState(FUT_GAME_MODE)
                    setFlowSubState(SETUP)
                    OSDKCards_Init
                    OnlineSession.SetGameMode(FUT2)
                    GenerateVersionString
                    FirstTimeInit
                    globalClasses.BackgroundManager.LoadBackground
                    ACTION_ADVANCE
```

This matches the live trace exactly: `LoginToFUT` called once,
`FirstTimeInit` called once, then `ACTION_ADVANCE` -- which is why the flow is
sitting on `fcc_login1` rather than `fcc_login`. `futLogIn0` runs to
completion. It also explains `FirstTimeInit`: `fcc_login::Login()` calls it as
one step among several, and `0x8909FC40` shows it registering ION viewmodels.

So the stall is entirely inside `fcc_login1`, at
`CardsLoginHelper.Init(..., OnError, InitialLoginDone)` /
`DoInitialLoginSteps`.

## What CardsLoginHelper is, and is not

* not an `ION_FCC` method -- absent from the API name table;
* not a file in `cards0.big` -- that archive has no `classes/` entry at all
  (only `artassets`, `background`, `components`, `imgassets`, `main`, `menus`,
  `overlays`, `screens`);
* named in exactly one place across every entry that decodes:
  `fcc_login1.big` itself.

That last point is weaker than it looks: **97 of the 169 `ion_fut` script
entries fail to decode**, so a negative result from that scan is not
exhaustive.

## The remaining gap is the class loader

`data/ui/game/globalclasses/classloader.big` lives in the title-update archive
`data0.big` (offset `0x31D9C0`, `0x10738` bytes). It still does not decode:

```text
container: chunklzx v2, total=236000, chunk=262144, chunks=1
chunk:     stored=67336, block_type=3 (LZX), data at 0x30

window 15/17/19/20  Invalid Huffman code
window 16/18/21     match reaches before the start of the window
```

Windows 18 and 21 decode symbols before failing on a match distance, so the
bitstream is being read as LZX correctly for a while; the failure is not at
the first symbol. The E8 translation header is already handled, so that is not
the cause. This is the one place a script-side class registry could still be
hiding, and it is the last undecoded thing on the login path.
