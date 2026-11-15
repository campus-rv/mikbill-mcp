"""CI check: every dependency named in pyproject.toml is pinned in the lock files.

A dependency added to pyproject.toml without running scripts/lock.sh would otherwise be silently missing from the
image (the Dockerfile installs only what requirements.lock lists) and from CI.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def names(requirements: list[str]) -> set[str]:
    return {re.split(r"[<>=!~ ;\[]", r, maxsplit=1)[0].lower().replace("_", "-") for r in requirements}


def locked(path: str) -> set[str]:
    text = (ROOT / path).read_text(encoding="utf-8")
    return {m.group(1).lower().replace("_", "-") for m in re.finditer(r"^([A-Za-z0-9_.-]+)==", text, re.M)}


def main() -> int:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    runtime, dev = names(project["dependencies"]), names(project["optional-dependencies"]["dev"])
    missing = sorted((runtime - locked("requirements.lock")) | ((runtime | dev) - locked("requirements-dev.lock")))
    if missing:
        print(f"not in the lock files, run scripts/lock.sh: {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"lock files cover {len(runtime)} runtime and {len(dev)} dev dependencies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
