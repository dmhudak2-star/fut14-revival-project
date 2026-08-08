# "EAS FC non connecté"

What the header banner means, and why it is a different problem from FUT.

## It is a second Blaze connection, not an HTTP one

`powdllzf.xex.dll` is mapped at `0x89700000` once the title is up, and its
strings name the whole subsystem:

```text
0x89706B08  POWService::PowBlazeDisconnected
0x8970E3A0  connectedToPOW
0x8970E38C  reconnectingToPOW
0x8970D128  connectionState
0x89706250  pal.gt.easfc.ea.com:8094          the session endpoint
0x897061B0  content.lt.easfc.ea.com:8080      the catalogue endpoint
```

`PowBlazeDisconnected` is the important one: the EAS FC session is a **Blaze**
connection to its own server, separate from the one FUT uses. Neither of those
hostnames is among the four the launch patch rewrites -- those are only
`gosredirector.ea.com` and its three siblings -- so the client resolves them for
real, reaches nothing, and reports the banner. There is no error in our journal
because the traffic never comes near us.

## What was changed

The module reads its endpoints from configuration in preference to those
compiled defaults:

```text
0x897085C4  ONLINE/POW_CUSTOMCONTENTURL
0x897085EC  ONLINE/POW_CUSTOMURL
0x897085B4  FIFA_POW_URL
0x89708598  FIFA_POW_CONTENT_SERVER_URL
0x8970857C  FIFA_POW_NUCLEUS_PROXY_URL
```

`OSDK_CORE` now serves all five, pointing the session at the Blaze core port
and the catalogue at the identity server. The retail values give the format:
`host:port` for the session, a URL for the content.

## Unverified

This is a configuration change made from static strings; it has not been seen
to work. If the banner still reads disconnected on the next launch, the next
step is to watch for a connection attempt on the Blaze port from the POW module
rather than assume the key was read at all -- and, failing that, to patch the
hostname in `powdll`'s image the way the launch patch already rewrites the four
in `default.xex`.

It is worth remembering that this is cosmetic for playing: FUT logs in, the club
loads, the market works and packs open with the banner reading disconnected
throughout.
