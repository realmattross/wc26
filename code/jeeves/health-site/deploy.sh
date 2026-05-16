#!/usr/bin/env bash
# Deploy helper for the personal health dashboard.
#
# Run from anywhere:
#   bash ~/Code/jeeves/health-site/deploy.sh
#   bash ~/Code/jeeves/health-site/deploy.sh "tweaked the colours"
#
# What it does (in order):
#   1. cd to the Jeeves repo root
#   2. Run the build script: regenerates data.json + insights.json
#   3. Stage the changed files
#   4. Commit with the message you passed (or a default)
#   5. Push to whatever remote is configured
#
# It fails loudly if anything's missing (no git remote, no GitHub
# credentials, build script error) so you know exactly what to fix.

set -euo pipefail

cd "$(dirname "$0")/.."   # repo root
REPO_ROOT="$(pwd)"
COMMIT_MSG="${1:-health-site: manual deploy $(date '+%Y-%m-%d %H:%M')}"

echo "==> Repo: $REPO_ROOT"
echo "==> Building data.json + insights.json…"
python3 scripts/build_health_site.py

if [[ ! -f health-site/public/data.json ]]; then
  echo "❌ build_health_site.py did not produce data.json — aborting."
  exit 1
fi

# Make sure we're in a git repo with a remote configured.
if ! git rev-parse --git-dir > /dev/null 2>&1; then
  echo "❌ Not inside a git repo. Run:"
  echo "     cd $REPO_ROOT"
  echo "     git init && git add . && git commit -m 'initial'"
  echo "     gh repo create jeeves --private --source=. --remote=origin --push"
  exit 1
fi

if ! git remote get-url origin > /dev/null 2>&1; then
  echo "❌ No 'origin' remote. Either:"
  echo "     gh repo create jeeves --private --source=. --remote=origin --push"
  echo "   or manually:"
  echo "     git remote add origin git@github.com:YOURUSER/jeeves.git"
  exit 1
fi

echo "==> Staging health-site/…"
git add health-site/

if git diff --cached --quiet; then
  echo "==> Nothing to commit — everything is already up to date."
  exit 0
fi

echo "==> Committing: $COMMIT_MSG"
git commit -m "$COMMIT_MSG"

echo "==> Pushing to origin…"
git push

echo ""
echo "✅ Pushed. Netlify should auto-deploy within ~30 seconds."
echo "   Open your Netlify dashboard to watch the build."
