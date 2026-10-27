#!/usr/bin/env bash
# Usage: scripts/new-worktree.sh <name> <branch>   -> ../reachout-wt/<name> on a new branch off main.
# Shares the main checkout's .venv and node_modules by symlink (8 GB machine: no second copy).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
dir="$root/../reachout-wt/$1"
git -C "$root" worktree add "$dir" -b "$2" main
ln -s "$root/.venv" "$dir/.venv"
ln -s "$root/desktop/node_modules" "$dir/desktop/node_modules"
echo "$dir"
