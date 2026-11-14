#!/bin/bash
# One-time installer, started in the background by ensure.sh. Safe to re-run.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$HOME/.skillspector-watch"
LABEL="${SG_LABEL:-com.salience.skillspector-watch}"  # SG_LABEL only for testing
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
# Pinned SkillSpector release (NVIDIA/SkillSpector). Download as an archive: no git needed.
SKILLSPECTOR_URL="https://github.com/NVIDIA/SkillSpector/archive/c81615007d40e86051a302a3cf897f8a05264928.tar.gz"
trap 'rm -f "$BASE/.installing"' EXIT
echo "=== $(date) install start"
mkdir -p "$BASE" "$HOME/Library/LaunchAgents"
cp -f "$ROOT/scripts/watch.py" "$BASE/watch.py"

# 1. uv (Python tool manager from Astral), into ~/.local/bin
UV="$HOME/.local/bin/uv"
if [ ! -x "$UV" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
fi

# 2. SkillSpector, in its own Python 3.12 environment
"$UV" tool install --force --python 3.12 "skillspector @ $SKILLSPECTOR_URL"
PY="$HOME/.local/share/uv/tools/skillspector/bin/python"
"$HOME/.local/bin/skillspector" --version

# 3. Background watcher (launchd): every 10 min + whenever a skill folder changes
WATCH_PATHS=""
for d in "$HOME/.claude/skills" "$HOME/.claude/plugins" "$HOME/.claude/agents" "$HOME/.claude/commands" \
         "$HOME/Documents/Salience HQ" "$HOME/Salience HQ"; do
  [ -d "$d" ] && WATCH_PATHS="$WATCH_PATHS<string>$d</string>"
done
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>-I</string><string>$BASE/watch.py</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>WorkingDirectory</key><string>$BASE</string>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>600</integer>
  <key>WatchPaths</key><array>$WATCH_PATHS</array>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>$BASE/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$BASE/launchd.err.log</string>
</dict></plist>
EOF
plutil -lint "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"   # RunAtLoad starts the first (baseline) scan
echo "=== $(date) install done"
osascript -e 'display notification "Installed. Scanning your existing skills now; you will get a summary when done." with title "Salience Guard"' || true
