#!/usr/bin/env python3
"""The second layer: an expanded portal on disk is, by definition, a bug.

`BLK_<ID>[` in a tracked file means a view state got written down. The block store still
holds the live claim, but this file no longer points at it — it holds a copy that will
never be corrected. Case is what makes this a one-line grep instead of a judgement call:
lowercase `blk_` is the on-disk truth, uppercase `BLK_` is the fill.

WHY A SECOND LAYER AT ALL. `contract-write.py` is the fix; this is the proof the fix
held. It covers the paths layer 1 cannot see — the known one being a `Bash` heredoc,
which never fires `PreToolUse:Write|Edit` — and, more usefully, the ones nobody has
thought of yet. A determined agent can still defeat both by writing through `Bash` and
committing nothing; that residual is real, bounded, and detectable at the next commit,
which beats claiming a guarantee we do not have.

Usage:
    python3 leak-check.py                 # every tracked file in the repo at $PWD  (CI)
    python3 leak-check.py --staged        # only what is about to be committed  (pre-commit)
    python3 leak-check.py FILE [FILE...]  # just these
    python3 leak-check.py --quiet         # exit code only

Exit 1 on a leak, 2 on a check that could not run — never 0 for either.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import portal_syntax  # noqa: E402

MAX_BYTES = 4_000_000


def tracked(root: Path, staged: bool) -> list[Path]:
    """Every tracked file, or — for a pre-commit hook — only what is being committed.

    A pre-commit pass over 13k files is a tax on every commit in the enclosing repo for a
    property that can only change in the files being written. `--staged` is the honest
    scope there; the full scan belongs in CI, where it also catches anything that reached
    disk by a path nobody has thought of yet.
    """
    args = (["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
            if staged else ["ls-files", "-z"])
    out = subprocess.run(["git", "-C", str(root), *args],
                         capture_output=True, text=True, check=True)
    return [root / p for p in out.stdout.split("\0") if p]


def leaks(paths: list[Path]) -> list[tuple[Path, int, str]]:
    found = []
    for p in paths:
        try:
            if not p.is_file() or p.stat().st_size > MAX_BYTES:
                continue
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue                               # binary or unreadable: not our business
        if "BLK_" not in text:                     # the cheap gate
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if portal_syntax.LEAK_RE.search(line):
                found.append((p, i, line.strip()[:120]))
    return found


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    args = [a for a in argv if not a.startswith("--")]

    if args:
        paths = [Path(a) for a in args]
        root = Path.cwd()
    else:
        # The repo under test is the one the CALLER is standing in — a pre-commit hook
        # and a CI step both run at the checkout root. Resolving it from `__file__`
        # instead scans the repo this handler happens to live in, which reports clean
        # about a repository nobody asked about. That is worse than no check, and it is
        # what this suite watched happen before the red test named it.
        root = Path.cwd()
        try:
            root = Path(subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, check=True).stdout.strip())
            paths = tracked(root, "--staged" in argv)
        except (subprocess.CalledProcessError, OSError) as e:
            # Never report clean from a failed check.
            print(f"leak-check: could NOT enumerate tracked files ({type(e).__name__}) — "
                  f"this is not a pass, it is an unrun check", file=sys.stderr)
            return 2

    found = leaks(paths)
    if not found:
        if not quiet:
            print(f"leak-check: clean — {len(paths)} file(s), no expanded portals on disk")
        return 0

    print(f"leak-check: {len(found)} expanded portal(s) written to disk — "
          f"each is a live claim frozen into a dead copy", file=sys.stderr)
    for p, i, line in found:
        try:
            shown = p.relative_to(root)
        except ValueError:
            shown = p
        print(f"  {shown}:{i}: {line}", file=sys.stderr)
    print("\nFix: replace each BLK_<ID>[...]{...}BLK_<ID> with its bare blk_<ID>. "
          "If this reached disk through a Bash heredoc, that is the known hole in "
          "contract-write.py, not a surprise.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
