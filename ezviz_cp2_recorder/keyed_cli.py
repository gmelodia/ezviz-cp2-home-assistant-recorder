from __future__ import annotations

import os
from pathlib import Path
import sys

KEY_FILE = Path(os.environ.get("PYEZVIZ_KEY_FILE", "/config/cp2_media_key.txt"))


def main() -> int:
    if not KEY_FILE.is_file():
        print(f"ERROR: camera key not found: {KEY_FILE}", file=sys.stderr, flush=True)
        return 2

    key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        print(f"ERROR: camera key is empty: {KEY_FILE}", file=sys.stderr, flush=True)
        return 2

    from pyezvizapi.client import EzvizClient

    def cached_get_cam_key(
        self: EzvizClient,
        serial: str,
        smscode: str | int | None = None,
        max_retries: int = 0,
    ) -> str:
        del self, serial, smscode, max_retries
        return key

    EzvizClient.get_cam_key = cached_get_cam_key  # type: ignore[method-assign]

    import pyezvizapi.__main__ as cli

    return int(cli.main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
