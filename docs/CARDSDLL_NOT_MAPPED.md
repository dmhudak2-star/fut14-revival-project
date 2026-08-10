# CardsDLLzf.xex.dll stopped being mapped

> **Superseded for the load case, 2026-08-10.** CardsDLL maps reliably again
> through `tools/fut.sh`, and full FUT sessions run: club, packs, market,
> cups. Everything below is about a period when it would not load at all, and
> is kept for the measurements it records.
>
> A different failure was seen on 2026-08-10 and is recorded at the end of this
> file: the module loads, FUT runs, and then the module is **unloaded
> mid-session**.

Since the console was restarted on 2026-08-07, `CardsDLLzf.xex.dll` has not
loaded once. It last mapped at 00:01 that night. Without it there is no FUT
login at all: no `/ut/auth`, no security question, no persona adoption, and
none of `LoginToFUT`, `FirstTimeInit` or anything downstream. Every analysis
of the `fcc_login1` loading dialog is therefore about a screen that is waiting
on a module which is not there.

This file records what was measured, so the same ground is not covered again.

## What the title does instead

It reaches the FUT launcher and stays there, looping over

```text
GET  /connect/auth
POST /authentication360
GET  /futBoot.xml
GET  /ut/game/fifa14/user/accountinfo
```

## Ruled out by measurement

* **The DLC configuration.** `OSDK_CLIENT` is fetched and `DLC_USE_REAL_DLL_LOAD`
  is served as `"1"`, confirmed by journalling the served map. The rest of the
  map -- FUT base URLs, `FUT_ENABLE_MENU`, `ONLINE/NO_ASSET_UPDATE`,
  `FUT_SKIP_ICEBREAKER_FLOW=0` -- is present and correct.
* **The DLC manager's gate.** On the `0xD1C01001` event its `owner+5` byte is
  `0`, which permits the load, and it reports one registered item.
* **The automatic-DLC route.** That loop only loads items declaring the key
  `dll`. `dlc_CardsDLL/info.dlc` declares `fut_dll = CardsDLL` and
  `fut_version = 1`, so CardsDLL was never that route's job. Reading a zero
  hit count on `dlc_automatic_item` says nothing about CardsDLL.
* **Script failure.** The UXLua error trace records no missing function and no
  missing module.
* **The profile.** Signing `Imskobogota6z` in changed nothing, and the menu
  still shows `EAS FC non connecté` either way.
* **Persisted account state.** Clearing the saved user settings and the
  security opt-in flags changed nothing.
* **`accountinfo`'s persona list.** Reporting an empty list -- what the PC
  reference serves -- made the run strictly worse, and restoring the populated
  persona did not bring the module back. Neither shape loads it.
* **The FutCfg DLC key.** Serving the reference's `1/1` in place of this
  build's `2/0` changed nothing over a full run.
* **Our own hooks.** The notification-bus and SendNavEvent traces read back as
  `original` after each title relaunch, so they are not resident when this
  happens.
* **Files on disk.** Nothing under the game directory has changed since the
  runs that worked: `data1` dates from 2026-08-01, `data0` from 2026-08-05,
  `default.xex` and `CardsDLLzf.xex.dll` from 2013.

## A caveat worth keeping

`load_image_path` (`0x823E8A88`) reports zero hits, yet `powdllzf.xex.dll`
**is** mapped in the same session. So that probe is not on the universal XEX
load path for this build, and its zero count is weaker evidence than it looks.
Two conclusions drawn from it earlier were overstated.

## What has not been tried

The one comparison that would settle this is a diff against a session where
the module did map. The only surviving artefact of one is
`runtime/overnight/cycle-20260807-000126.log`; its server journal is
`runtime/live-easw-v46.jsonl`. Everything above was reasoned forward from the
failing side.

## 2026-08-10: unloaded mid-session

A different failure, and the one that is live. The module loaded, a full FUT
session ran -- club, cups, five packs opened and sent to the club -- and then
the title stopped talking to the server. What "disconnected" means natively:

```text
modules  ->  powdllzf.xex.dll   still mapped
             CardsDLLzf.xex.dll GONE
0x89000000 .. 0x892B0000        unmapped, reads return Invalid memory
```

The title itself kept running and XBDM answered again after about two minutes.
So this is not a crash: the FUT session tore itself down and took the card
engine with it. The client's club model goes with it, which is why cards that
the server still holds -- verified, 453 items served, the twelve latest among
them in state `free` -- looked lost from inside the game.

### The last thing served before it went

```text
13:58:18  GET /ut/game/fifa14/club   ->  244 486 bytes
13:58:54  GET /ut/game/fifa14/club/stats/*
          (nothing further)
```

Asked for the club with no filter, this server returns every card in one
document. At 453 cards that is 244 KB of JSON on a 2005 console, and it grows
with every pack opened. `club_response` pages only when the client sends a
`count`; with no query it has no bound at all.

This looked like the cause. Measuring it across every journal mostly killed it:

```text
v62  08:20-08:23   11 x 77 440 bytes   console kept going, 28-84 requests after
v63  08:36         7 x 75 136 bytes    console kept going, 26-38 requests after
v64  08:43         7 x 77 440 bytes    last one: 0 requests after
now  13:58:18      1 x 244 486 bytes   0 requests after
```

So an unfiltered club response is not fatal in itself -- eighteen of them were
served and survived. What is different here is the **size**: 244 KB against 77,
because the club has grown to 453 cards. That is consistent with a threshold
and it rests on a single observation at that size, which is not enough to call
it the cause.

Worth bounding anyway: a response that grows without limit as the club grows is
a defect on its own terms. But bounding it should not be described as fixing
this until a long session survives one.

The distinguishing test: play to another teardown while watching the size. If
it lands near 244 KB again there is a threshold; if it lands elsewhere, this is
a coincidence and the cause is something else entirely.

### What this blocks

`GetCardDuplicate` cannot be traced while the module is unmapped -- its code
lives in that range. Any duplicate-flag work needs a live FUT session first.

### A sibling build saw the same failure mode

The PC revival's `settle_match_end` carries this, about its own
`/match/end` response:

> The BETA 2.17 live forfeit proved the match itself is healthy but the client
> **disconnects immediately after parsing our oversized destroy response**.

Different route, same shape of failure: a response the client parses
successfully and then drops the connection over, because of its size. That is
the mechanism this file's 244 KB club response was suspected of, and it is
independent evidence that the mechanism is real on this title -- not that it
is what happened here.

Their remedy was to emit only the three members the parser recovers and keep
everything else server-side. `/match/end` here now does the same.
