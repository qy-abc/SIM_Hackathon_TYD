"""Run script: python -m closer.cli generate | eval"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from closer.data.generator import DEFAULT_SEED, write_month
from closer.eval.golden import load_key, score_commission

GOLDEN_DIR = Path("data/golden")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="closer")
    sub = parser.add_subparsers(dest="cmd", required=True)
    gen = sub.add_parser("generate", help="write the golden month to data/golden/")
    gen.add_argument("--seed", type=int, default=DEFAULT_SEED)
    gen.add_argument("--out", type=Path, default=GOLDEN_DIR)
    ev = sub.add_parser("eval", help="score the rules engine against the answer key")
    ev.add_argument("--key", type=Path, default=GOLDEN_DIR / "answer_key.json")
    args = parser.parse_args(argv)

    if args.cmd == "generate":
        for name, path in write_month(args.out, args.seed).items():
            print(f"wrote {path}")
        return 0
    if not args.key.exists():
        print(f"{args.key} not found; run `python -m closer.cli generate` first", file=sys.stderr)
        return 2
    report = score_commission(load_key(args.key))
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
