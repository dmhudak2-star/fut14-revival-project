# Safety and recovery

## Before connecting

- Use a console and game copy you own.
- Disconnect from Xbox Live for invasive tests.
- Back up game archives, launch configuration and profile data.
- Keep XBDM on a trusted LAN; port 730 is not an Internet-facing service.
- Never expose console keys, KV identifiers, account tokens or memory dumps.

## Risk classes

### Read-only

Directory listing, screenshots, section dumps, status readers and dry-run
preflights. These can still expose private data in their output, so do not post
raw results without review.

### Guarded live patches

Scripts that verify exact original bytes, own a dedicated code cave and provide
an explicit restore action. Use only on the supported build.

### Research-only / crash-prone

Breakpoint traces, receive-pump injections, ProtoSSL/QoS experiments, direct
JRPC UI calls and broad connection-state bypasses. A crash or title restart is
expected. These scripts remain for auditability, not as a quickstart.

## Do not use

The following former experiment was intentionally excluded from this
repository:

- `fifa14_enterfut2_clean_retry.py`: it treated manager `+0x114` as a pending
  WebSession flag and cleared it. Later disassembly shows `+0x114` is an
  in-flight state in a broader OSDK/EASW download/configuration object; it is
  not a proven FUT-ready flag. Clearing it can duplicate initialization and
  freeze the title.

The earlier `fifa14_cards_ui_init_once.py` was superseded by the guarded v2
implementation and is also excluded.

## Live-patch recovery

1. Do not repeatedly re-run an apply command after a timeout.
2. Reconnect with a fresh XBDM session and run the script's `status` action.
3. Restore only when the script recognizes either its exact patch or the exact
   original bytes.
4. If the site contains foreign bytes, stop. Do not overwrite them blindly.
5. If the title is frozen, unload/restart the title; most code patches are
   volatile.

## Archive recovery

Never rename active archives while FIFA 14 is mapped. The transactional helper:

- refuses to run while the supported `default.xex` is loaded;
- never deletes or overwrites a destination;
- keeps original archives under explicit backup names;
- validates size and SHA-256;
- converges back to the original state after an interrupted transaction.

Expected hashes in that helper describe one researcher's exact files. Review
and regenerate them for another legally obtained copy.

## Repository hygiene

Never commit:

- `.xex`, `.big`, `.bh`, `.nav`, compressed chunks or language archives;
- memory dumps, packet captures, screenshots or raw session logs;
- Cipher/xbGuard/stealth files or configuration;
- XBDM/JRPC binaries;
- console identifiers, profiles, KV data or account details.

Run `python3 scripts/repo_safety_check.py` before every push.
