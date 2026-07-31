#!/usr/bin/env python3
"""Fail if a repository candidate contains forbidden artifacts or obvious PII."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {
    ".xex", ".dll", ".exe", ".big", ".bh", ".nav", ".bin", ".dump",
    ".dmp", ".mem", ".pcap", ".pcapng", ".iso", ".png", ".jpg",
    ".jpeg", ".heic", ".zip", ".7z", ".rar",
}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml", ".ini", ".yml", ".yaml"}
PATTERNS = {
    "local user path": re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    "private LAN address": re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    "console KV-style identifier": re.compile(r"\bXE\.\d{8,}\b"),
    "private key marker": re.compile(r"BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY"),
}


def main() -> int:
    errors: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative = path.relative_to(ROOT)
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden artifact: {relative}")
        if path.stat().st_size > 10 * 1024 * 1024:
            errors.append(f"file exceeds 10 MiB: {relative}")
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {
            "LICENSE", ".gitignore"
        }:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"non-UTF-8 text candidate: {relative}")
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{label}: {relative}")
    if errors:
        print("Repository safety check failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("Repository safety check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
