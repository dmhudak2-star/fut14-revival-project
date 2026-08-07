# CardsDLLzf.xex.dll stopped being mapped

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
