# Contributing

Contributions should be reproducible and reversible.

Include:

- exact platform/build signature;
- original bytes or structural invariant (not a proprietary binary);
- reason the address/function was identified;
- preconditions and postconditions;
- rollback behavior;
- synthetic tests where practical.

Do not contribute game assets, executables, firmware, stealth-service files,
memory captures, packet captures containing user data or credentials.

Run:

```bash
python3 -m compileall -q tools scripts
python3 scripts/repo_safety_check.py
```

