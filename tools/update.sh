#!/bin/bash
# update.sh: one unattended refresh. Scrape Hugging Face, rebuild the dashboard data, commit, push if a remote exists.
set -uo pipefail
cd "$(dirname "$0")/.."
exec 9> .git/update.lock; flock -n 9 || { echo "another update is running"; exit 0; }
echo "== $(date -u +%FT%TZ) update start"
python3 tools/scrape-hf.py || echo "scrape had failures rc=$? (retried next run)"
python3 tools/scrape-pf.py ${PF_ARGS:-} || echo "pirate face check had failures rc=$? (retried next run)"
python3 tools/build-dashboard.py || echo "dashboard failed rc=$?"
git add -A
if git diff --cached --quiet; then echo "no changes"; else
  n=$(git diff --cached --numstat -- ledger/EVENTS.tsv | cut -f1)
  summary=$(git diff --cached -U0 -- ledger/EVENTS.tsv | grep '^+[0-9]' | cut -f3 | sort | uniq -c | awk '{printf "%s%s %s", (NR>1?", ":""), $1, $2}')
  git commit -q -m "Automated update: ${n:-0} new event(s)${summary:+ ($summary)}" && echo "committed $(git rev-parse --short HEAD)"
fi
if git remote get-url origin >/dev/null 2>&1; then git push -q origin HEAD && echo pushed || echo "PUSH FAILED"; fi
echo "== $(date -u +%FT%TZ) update end"
