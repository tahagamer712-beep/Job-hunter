#!/data/data/com.termux/files/usr/bin/bash
# Job Hunter — 24/7 supervision loop for Termux.
#
# Requires `flock` (process locking, H11 fix) — on Termux:
#   pkg install util-linux
#
# Usage:
#   chmod +x run_forever.sh
#   ./run_forever.sh              # runs in foreground
#   termux-wake-lock              # (run once) prevents Android from killing Termux
#   nohup ./run_forever.sh &      # or run in background
#
# Restarts the bot automatically if it crashes, with a backoff to avoid a
# crash-loop hammering the CPU, and rotates logs periodically so they don't
# grow unbounded.

set -u
cd "$(dirname "$0")"

PID_FILE="job_hunter.pid"
LOCK_FILE="job_hunter.lock"
RESTART_DELAY=10
MAX_RESTART_DELAY=300
LOG_ROTATION_INTERVAL_LOOPS=20   # rotate roughly every N restarts/loop checks

# H11: prevent a second instance of this script from running concurrently
# against the same database (two bots polling the same Telegram token and
# writing to the same SQLite file is a real corruption/race risk).
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "$(date -Iseconds) - another instance is already running (lock held on $LOCK_FILE) — exiting." >> logs/supervisor.log
    echo "Another instance of Job Hunter appears to be running already (lock file: $LOCK_FILE)." >&2
    exit 1
fi

echo $$ > "$PID_FILE"
trap 'rm -f "$PID_FILE"; exit 0' INT TERM

delay=$RESTART_DELAY
loop_count=0

while true; do
    echo "$(date -Iseconds) - starting Job Hunter bot" >> logs/supervisor.log

    python3 src/bot/main.py >> logs/supervisor.log 2>&1
    exit_code=$?

    echo "$(date -Iseconds) - bot exited with code $exit_code" >> logs/supervisor.log

    loop_count=$((loop_count + 1))
    if [ $((loop_count % LOG_ROTATION_INTERVAL_LOOPS)) -eq 0 ]; then
        python3 -m src.log_rotation >> logs/supervisor.log 2>&1
    fi

    if [ "$exit_code" -eq 0 ]; then
        # Clean exit (e.g. owner stopped it deliberately) — don't restart.
        echo "$(date -Iseconds) - clean exit, not restarting" >> logs/supervisor.log
        break
    fi

    echo "$(date -Iseconds) - restarting in ${delay}s" >> logs/supervisor.log
    sleep "$delay"

    # Exponential backoff on repeated crashes, capped at MAX_RESTART_DELAY.
    delay=$((delay * 2))
    if [ "$delay" -gt "$MAX_RESTART_DELAY" ]; then
        delay=$MAX_RESTART_DELAY
    fi
done

rm -f "$PID_FILE"
