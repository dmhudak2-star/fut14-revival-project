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

## The seven minutes, from the journals

Blaze bursts are collapsed per second and identical consecutive lines
dropped; `cN` is a component id. Everything else is verbatim.

```text
14:55:50  blaze burst: c5x2, c9x18
14:55:50  connected
14:55:50  disconnected
14:55:51  authentication2_login
14:55:51  blaze burst: c1x4, c7x6, c9x20, c10x2, c11x4, c15x8, c21x2, c25x2, c35x2, c2076x2, c2249x4, c2268x2, c30722x9
14:55:51  easw_auth_request
14:55:51  identity_http_redirect
14:55:51  HTTP GET /connect/auth
14:55:51  HTTP POST /authentication360
14:55:51  user_setting_load
14:55:51  user_settings_load_all
14:55:52  blaze burst: c9x2
14:55:52  fut_boot_served
14:55:52  HTTP GET /futBoot.xml
14:55:52  user_setting_save
14:56:12  blaze burst: c9x2
14:56:15  fut_account_info_request
14:56:15  HTTP GET /ut/game/fifa14/user/accountinfo
14:56:32  blaze burst: c9x2
15:03:35  fut_icebreaker_packlist_served
15:03:35  HTTP GET /fut/packs/icebreaker/icebreakerpacklist.json
15:03:37  blaze burst: c5x2
15:03:37  connected
15:03:37  connection_error
15:03:37  disconnected
15:03:41  authentication2_login
15:03:41  blaze burst: c1x2, c7x6, c9x34, c10x2, c11x2, c15x6, c21x2, c25x2, c35x2, c2076x2, c2249x4, c2268x2, c30722x9
15:03:41  connected
15:03:41  easw_auth_request
15:03:41  identity_http_redirect
15:03:41  HTTP GET /connect/auth
15:03:41  HTTP POST /authentication360
15:03:41  user_settings_load_all
15:03:42  blaze burst: c1x2, c9x6, c11x2, c15x2
15:03:42  fut_boot_served
15:03:42  HTTP GET /futBoot.xml
15:03:42  user_setting_load
15:03:42  user_setting_save
15:04:02  blaze burst: c9x2
15:04:08  fut_account_info_request
15:04:08  HTTP GET /ut/game/fifa14/user/accountinfo
15:04:22  blaze burst: c9x2
15:11:28  disconnected
15:11:30  identity_http_listening
15:11:30  listening
15:11:30  ready
15:11:35  fut_icebreaker_packlist_served
15:11:35  HTTP GET /fut/packs/icebreaker/icebreakerpacklist.json
```

Three things stand out.

The client goes quiet for **seven minutes** after `accountinfo` at 14:56:15 —
nothing but the two-frame Util keepalive every twenty seconds — and only then
asks for the pack list.

The pack list is followed two seconds later by `connection_error` and a
dropped Blaze connection, then a complete re-bootstrap at 15:03:41. So the
title did not sit on the captain selector: it asked for the data and fell over
immediately.

`/ut/auth` appears nowhere in the window. The FUT login never ran.

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
