"""Print a deterministic quarter of API test files, relative to the repository root."""

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PINNED = Path("apps/api/tests/requisitions/test_exactly_once_property.py")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shard", type=int)
    parser.add_argument("total", type=int)
    args = parser.parse_args()
    if not 1 <= args.shard <= args.total:
        parser.error("require 1 <= shard <= total")
    files = sorted(path.relative_to(ROOT) for path in (ROOT / "apps/api/tests").rglob("test_*.py"))
    for index, path in enumerate(files):
        shard = 1 if path == PINNED else index % args.total + 1
        if shard == args.shard:
            print(path.as_posix())


if __name__ == "__main__":
    main()
