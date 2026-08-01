# Method used to reach the current milestone

## 1. Establish a recoverable baseline

1. Keep untouched copies of every title file.
2. Record file sizes and SHA-256 hashes.
3. Confirm XBDM connectivity on port 730.
4. Record `modules` and `modsections` before using any address.
5. Keep the console and analysis computer on a trusted private LAN.

The supported build exposes `default.xex` at `0x82000000` with timestamp
`0x534C8977`. Stop on any mismatch.

## 2. Capture the natural failure

Capture from cold start through the first FUT click. Useful display filters:

```text
ip.addr == XBOX_IP
ip.addr == 159.153.52.75 && udp.port == 3074
dns or http or tls
```

The natural capture showed bidirectional EA traffic and Xbox/Microsoft service
traffic. The final EA popup was therefore treated as an application-flow
failure rather than proof of a firewall block.

## 3. Dump only what is needed

```bash
python3 tools/xbox360_xbdm_dump.py XBOX_IP --sections rdata
python3 tools/xbox360_xbdm_dump.py XBOX_IP --sections text data
python3 tools/xbox360_xbdm_dump.py XBOX_IP \
  --sections pdata edata idata xbld
```

Runtime dumps are private research artifacts. Never commit them.

The same process was used for `powdllzf.xex.dll`; XAM was dumped separately to
resolve networking exports.

## 4. Identify the FUT entry points

Static string cross-references and PowerPC disassembly connected:

- `EnterFUT2` to wrapper `0x82DA6850` and handler `0x828350C8`.
- `LoadFUTSkipBlaze` to `0x82805D30`.
- the Cards root global and its native initializer.
- ION `SendNavEvent` to interface global `0x83DA4604` and method
  `0x82805C10`.

Every live probe first verified the expected vtable and original instruction
bytes.

## 5. Create Cards state natively

The important invariant was to let the title construct the Cards children
instead of fabricating pointers. `fifa14_state1_failover_slot.py` temporarily
relays one natural failure state to the native state-2 slot, then restores the
slot immediately.

Success requires:

- root vtable `0x89708AE0`;
- root `+0x80 == 1`;
- non-null children at `+0x3A08`, `+0x3A0C`, `+0x3A10`, `+0x3A14`;
- auth vtable `0x89707078`.

Do not set `+0x80` back to zero after success.

## 6. Bypass only the Blaze gate

The transactional patch changes exactly two sites:

- `0x82805D30`: `LoadFUTSkipBlaze` returns true (`li r3,1; blr`).
- `0x82835198`: EnterFUT2 uses its fast path.

The script validates the original tail, existing Cards hook and current auth
object, applies in a safe order and rolls back through a fresh XBDM connection
if an acknowledgement is lost.

## 7. Enter and observe FUT

After a single FUT selection:

1. Capture a screenshot through XBDM.
2. Read the Cards one-shot journal.
3. Read the `pow/auth` journal.
4. Poll loader readiness through `0x827C63B0`.
5. Inspect mounted `game:\` archives.

In the current run, readiness returned 1 and Cards was initialized, while
`pow/auth` remained untouched. This narrowed the problem to the external view
and navigation boundary.

## 8. Navigation probes

`fifa14_send_nav_event.py` validates:

- `[0x83DA4604]` is a title-heap object;
- vtable is `0x8206A64C`;
- vtable `+0x14` is `0x82805C10`;
- the active flow service chain is non-null and has the supported binding;
- the event string at the build-specific address matches exactly.

Use `--dry-run` first. If dispatching, send one event and observe before the
next. The stock archive describes this login flow sequence:

```text
futLauncher --advanceRequest--> futFlow/futLogIn0
futLogIn0   --advance---------> security
security    --advance---------> futLogIn1
futLogIn1   --createClub------> createClub
```

The archive active during the latest experiment differed at the first
transition only:

```text
futLauncher --FUTStartUp-----> futFlow/futLogIn0
futLogIn0   --advance--------> security
security    --advance--------> futLogIn1
futLogIn1   --createClub-----> createClub
```

`FUTStartUp` is not stored in the title's static rdata. The tool therefore
passes a temporary NUL-terminated byte array through JRPC2 after applying the
same object/vtable guards. In the observed run this event changed the FUT
stadium spinner into a native black `Chargement…` dialog, proving that the
patched launcher transition consumed it. A following direct `advance` was
accepted but did not visibly leave that dialog.

The method dispatches synchronously and is not formally proven main-thread
safe. Do not burst the sequence.

## 9. Inspect the pending ION view

Read the live load-view consumer before forcing another event. The important
evidence is the exact requested background, screen, sub-screen and popup state.
In the latest session the screen request was
`external/ion_fut/screens/FutCreateClub` and the popup state contained `ToFe`.

The ION investigation then proceeds from request to execution:

1. inspect dispatcher subscribers and event-to-action routes;
2. passively trace the `LoadView` enqueue sites;
3. passively trace the screen-executor `QueueAction` bridge;
4. determine whether the downstream flow-action dispatcher finds a matching
   handler;
5. only then inspect or reproduce the expected completion event.

Heap pointers observed in this process are valid only for the current title
session. Rebooting or unloading the title invalidates them.

## 10. Validate the CreateClub asset

Before treating the black loading popup as a missing file, extract the exact
entry from the Cards archive mounted by the title and validate every container
layer:

1. verify the BIG/BH offset and compressed length agree;
2. decode every XMem/LZX chunk rather than only the first chunk;
3. verify the decompressed output is a structurally valid inner BIGF archive;
4. enumerate its entries and search the ActionScript payload for the expected
   `FutCreateClub`, `CreateUser` and `CREATE_CLUB` symbols.

The observed entry decoded from three chunks to a valid 689,596 byte inner BIGF
package with ten entries. This rules out a simply missing or truncated
CreateClub payload, while leaving runtime dependencies and view-model bindings
open.

## 11. Register missing consumers only through native APIs

The observed `globalviewmodel` object existed but was detached from the live
dispatcher. Registration through the title's native API produced visible
front-end activity and restored the stadium background. It did not dismiss the
loading popup, so registration was necessary evidence but not a complete fix.

Avoid inserting list nodes or fabricating interface objects manually. Resolve
the manager on the native front-end tick, validate object/vtable relationships,
call the native register/enable path once, and journal the result.

## 12. Archive-based CreateClub experiment

As a separate, restart-based experiment, build a new `data1.big/.bh` from the
researcher's own files with:

```bash
python3 tools/archive/build_fifa14_createclub_patch.py \
  ORIGINAL_DATA1_BIG ORIGINAL_DATA1_BH \
  PATCHED_MAINFEFLOW_NAV PATCHED_FUTLOGINFLOW_NAV \
  OUTPUT_DATA1_BIG OUTPUT_DATA1_BH
```

The generated pair is intentionally ignored by Git. The console swap helper is
case-specific: review its expected hashes and filenames before using it on a
different dump.

## 13. Record evidence

For every test, record:

- build signature;
- exact original and patched bytes;
- object/vtable guards;
- pre/post state;
- screenshot or journal delta;
- cleanup result.

An unchanged popup without a journal delta is not evidence that the patched
path executed.
