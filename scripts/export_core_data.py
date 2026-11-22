#!/usr/bin/env python3
"""CLI entry for the local data backup (the MCP tool export_core_data
is the same code path — content_core/backup.py).

Usage:
    .venv/bin/python scripts/export_core_data.py           # export
    .venv/bin/python scripts/export_core_data.py --list    # list archives

Snapshots every local store into one timestamped zip under backups/
with a manifest (per-store counts, per-file sha256). Credential-
pattern files are excluded and recorded in the manifest.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from content_core import backup


def main() -> int:
    if "--list" in sys.argv:
        print(json.dumps({"backups": backup.list_backups()},
                         ensure_ascii=False, indent=2))
        return 0
    result = backup.export_core_data()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
