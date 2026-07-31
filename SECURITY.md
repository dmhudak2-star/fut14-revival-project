# Security policy

Do not open a public issue containing console keys, KV identifiers, account
tokens, IP addresses tied to a public endpoint, raw memory dumps or packet
captures. Redact the material and describe the issue at a high level.

XBDM is intended for a trusted private LAN. Never port-forward TCP 730 or the
local Blaze test ports to the Internet.

If sensitive data is committed, revoke/rotate it where possible and remove it
from Git history before pushing. Deleting the working-tree file alone is not
sufficient.

