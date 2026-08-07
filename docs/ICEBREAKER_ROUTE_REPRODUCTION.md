# Reaching the icebreaker pack list, and what is still unexplained

On 2026-08-07 the title requested
`/fut/packs/icebreaker/icebreakerpacklist.json` three times, at 15:03:35 and
twice at 15:11:35. That request appears in no other session in this project's
journals, across several days. This records exactly what was in place, because
the result has not reproduced since.

## Configuration at the time

* `data1.big` / `data1.bh`: **patched**, `futLogIn1 / advance` retargeted from
  `futLogIn2` to `iceBreaker`, written in place by
  `tools/archive/build_fifa14_icebreaker_route_patch.py`.
* `cards0.big` / `cards0.bh`: **retail** at 15:03; still retail at 15:11.
  The `fcc_login1` popup patch was *not* active for either request.
* Server: the local Blaze/HTTP server. At 15:03 it served the minimal pack
  list (`id` and `image` only); at 15:11 it served the full fixture, having
  been restarted at 15:11:30.
* `helperFunctions`: applied at the main menu before entering FUT, as the
  measurement cycle does.
* Console: title launched through `tools/fifa14_early_local_server.py` with
  `--redirector-transport plaintext --redirect-fut-resource`.

## The part that changes the reading

Neither request was preceded by `/ut/auth` in its session. The last `/ut/auth`
in any journal is 12:56:52, and the security question it leads to was answered
at 13:02:11. After that the client never authenticates again, and it is
exactly those sessions that ask for the pack list.

So the request is **not** downstream of a completed FUT login. The flow
reaches the icebreaker without logging in, asks for the captain data, fails to
build from it, and restarts its bootstrap six seconds later — the fresh
`connect/auth` at 15:03:41 is that restart.

That is consistent with the route patch doing its job too early: with
`futLogIn1 / advance` pointing at `iceBreaker`, an `advance` that fires before
`InitialLoginDone` lands on the captain selector instead of `futLogIn2`.

## Reproduction

```bash
# 1. Console at the dashboard, title not mapped.
# 2. Patched data1 in place, retail cards0:
#      data1.big / data1.bh          <- build_fifa14_icebreaker_route_patch.py
#      cards0.big / cards0.bh        <- retail
# 3. Launch with the redirects armed, then run one supervised cycle:
RUNS=1 zsh tools/fifa14_overnight_driver.sh
# 4. Watch only the server journal; reading the console framebuffer while the
#    title runs makes it stutter badly.
grep icebreakerpacklist runtime/live-easw-*.jsonl
```

## What has not reproduced

Three later runs with the same archives produced no pack list request, no
`/ut/auth`, and `total recorded calls = 0` from the FUT API trace: with the
route patch the title does not start its FUT login at all, where retail
archives reach `LoginToFUT`, `FirstTimeInit` and the security question.

The account state persisted after the 13:02 validation is the most likely
variable, since it is what changed between the sessions that still ran
`/ut/auth` and every session after. `runtime/local-account.json` holds it.
