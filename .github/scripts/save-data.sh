#!/usr/bin/env bash
# Commit trader/india to the data branch and push it. $1 is the commit message.
# GitHub sometimes answers a push with a 5xx, so the push is tried up to five times.
set -u
cd "$(dirname "$0")/../../trader/india" || exit 1

# stock lists older than 10 days are not needed again (dates from the names:
# a fresh checkout resets every file's mtime)
cutoff=$(date -d '10 days ago' +%F)
for f in liquid-*.json; do
  d=${f#liquid-}; d=${d%.json}
  if [ -f "$f" ] && [[ "$d" < "$cutoff" ]]; then rm "$f"; fi
done

git add -A -- . ':!.*'      # not the .paper-*/.state-* temp files of a write in progress
git diff --cached --quiet && exit 0
git commit -q -m "$1"
for i in 1 2 3 4 5; do
  if git pull -q --rebase origin data; then
    git push -q origin HEAD:data && exit 0
  else
    git rebase --abort 2>/dev/null
  fi
  echo "push to data failed (try $i of 5)"
  sleep $((i * 15))
done
exit 1
