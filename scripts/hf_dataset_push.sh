#!/usr/bin/env bash
#
# Push a SQLite DB to a Hugging Face dataset repo using Git + Git LFS.
#
# Why this script exists
# - HF dataset repos reject binary files unless tracked via Git LFS.
# - Remote repos often already have commits (README/init) causing non-fast-forward
#   push errors if you don't fetch/track the remote branch first.
#
# What this script does
# - Ensures Git + Git LFS are available; checks env + DB path
# - Initializes a local working repo at .hf-dataset (ignored by this project)
# - Adds/uses HF remote at https://huggingface.co/datasets/<account>/<dataset>
# - If the remote branch exists, fetches it and checks out a local branch tracking it
# - Ensures LFS tracking for *.db / digest.db, commits .gitattributes
# - Re-adds the DB so it’s stored as an LFS pointer, then rebase-pulls and pushes
# - Logs key steps so it’s clear what worked and what didn’t
#
# Usage (env)
#   HF_REPO=account/dataset HF_BRANCH=main DB_PATH=data/digest.db bash scripts/hf_dataset_push.sh
#
set -euo pipefail

REPO="${HF_REPO:-}"
BRANCH="${HF_BRANCH:-main}"
DB_PATH="${DB_PATH:-data/digest.db}"
WORKDIR="${WORKDIR:-.hf-dataset}"
MSG="${MSG:-update: upload SQLite}"

log() { echo "[hf] $*"; }

if ! command -v git >/dev/null 2>&1; then
  echo "git is required" >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git is required" >&2
  exit 1
fi

if ! command -v git-lfs >/dev/null 2>&1 && ! git lfs version >/dev/null 2>&1; then
  echo "git-lfs is required. Install from https://git-lfs.com and run: git lfs install" >&2
  exit 1
fi

if [ -z "$REPO" ]; then
  echo "Set HF_REPO=username/dataset_name (without the datasets/ prefix)" >&2
  exit 1
fi

if [ ! -f "$DB_PATH" ]; then
  echo "DB not found at $DB_PATH" >&2
  exit 1
fi

log "Repo=$REPO Branch=$BRANCH DB=$DB_PATH Workdir=$WORKDIR"

mkdir -p "$WORKDIR"
cd "$WORKDIR"

if [ ! -d .git ]; then
  log "Initializing working repo at $WORKDIR (LFS enabled)"
  git init
  git lfs install
  printf "*.db filter=lfs diff=lfs merge=lfs -text\n" > .gitattributes
  echo "# Dataset: $REPO" > README.md
  git add .gitattributes README.md
  git commit -m "init" || true
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  log "Adding HF remote: https://huggingface.co/datasets/$REPO"
  git remote add origin "https://huggingface.co/datasets/$REPO"
fi

# If remote branch exists, track it; else create new branch
if git ls-remote --exit-code origin "$BRANCH" >/dev/null 2>&1; then
  log "Remote branch exists; fetching and tracking origin/$BRANCH"
  git fetch origin "$BRANCH"
  git checkout -B "$BRANCH" "origin/$BRANCH"
else
  log "Remote branch missing; creating local branch $BRANCH"
  git checkout -B "$BRANCH"
fi

# Ensure LFS tracking is in place (update .gitattributes if needed)
git lfs install
git lfs track "*.db" "digest.db" >/dev/null 2>&1 || true
git add .gitattributes
log "Ensured LFS tracking for *.db (updated .gitattributes)"

# Copy DB and (re)add to index to ensure it's stored as an LFS pointer
cp -f "../$DB_PATH" ./digest.db
git rm --cached digest.db >/dev/null 2>&1 || true
git add digest.db
git commit -m "$MSG" || true

# Sanity log: confirm LFS pointer
if git lfs ls-files | grep -q " digest.db$"; then
  log "LFS pointer OK: $(git lfs ls-files | grep ' digest.db$' || true)"
else
  log "Warning: digest.db not listed by git lfs ls-files; push may be rejected"
fi

# Rebase on remote and push
log "Rebasing on remote and pushing to origin/$BRANCH"
git pull --rebase origin "$BRANCH" || true
git push -u origin "$BRANCH"
log "Pushed $DB_PATH to https://huggingface.co/datasets/$REPO (branch: $BRANCH)"
