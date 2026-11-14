#!/bin/bash
# Runs at every Claude Code session start (SessionStart hook). Fast when already installed.
# Keeps ~/.skillspector-watch/watch.py current and starts the one-time install if needed.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$HOME/.skillspector-watch"
LABEL="com.salience.skillspector-watch"
PY="$HOME/.local/share/uv/tools/skillspector/bin/python"
mkdir -p "$BASE"

# Always ship the latest watcher logic from the plugin.
cmp -s "$ROOT/scripts/watch.py" "$BASE/watch.py" || cp -f "$ROOT/scripts/watch.py" "$BASE/watch.py"

if [ -x "$PY" ] && launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  exit 0  # installed and running
fi

LOCK="$BASE/.installing"
if [ -f "$LOCK" ] && [ $(( $(date +%s) - $(stat -f %m "$LOCK") )) -lt 1800 ]; then
  echo '{"systemMessage":"Salience Guard: SkillSpector is still installing in the background. Custom skills stay blocked until their first scan."}'
  exit 0
fi
touch "$LOCK"
nohup /bin/bash "$ROOT/scripts/install.sh" >>"$BASE/install.log" 2>&1 </dev/null &
echo '{"systemMessage":"Salience Guard: installing SkillSpector in the background (about 5-10 minutes, one time). Custom skills stay blocked until they are scanned; you will get a Mac notification when it is ready."}'
exit 0
