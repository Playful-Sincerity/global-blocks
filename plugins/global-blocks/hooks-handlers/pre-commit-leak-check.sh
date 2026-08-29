#!/usr/bin/env bash
# Pre-commit: refuse a commit that would write an expanded portal to disk.
#
# `contract-write.py` is the fix; this is the proof it held. It covers the paths the
# PreToolUse hook cannot see — the known one being a Bash heredoc, which never fires
# PreToolUse:Write|Edit — and the ones nobody has thought of yet.
#
# INSTALL (from the repo you want protected):
#   ln -sf "<plugin>/hooks-handlers/pre-commit-leak-check.sh" .git/hooks/pre-commit
#   chmod +x .git/hooks/pre-commit
#
# CI does the full-repo scan instead of the staged one:
#   python3 <plugin>/hooks-handlers/leak-check.py
#
# Bypass, when you genuinely mean to commit an expanded form (a doc ABOUT the syntax):
#   git commit --no-verify
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/leak-check.py" --staged
