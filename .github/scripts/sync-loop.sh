#!/usr/bin/env bash
# While the trader runs, save its data to the data branch every 15 minutes, so a job
# that dies mid-day loses at most 15 minutes.
while sleep 900; do
  "$(dirname "$0")/save-data.sh" "run $(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M IST') (in progress)" \
    || echo "in-progress save failed; trying again in 15 minutes"
done
