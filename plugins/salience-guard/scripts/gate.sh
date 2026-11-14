#!/bin/bash
# Hook entry point. Picks a Python that will not trigger macOS's "install developer tools" prompt.
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$HOME/.local/share/uv/tools/skillspector/bin/python"
if [ -x "$PY" ]; then
  exec "$PY" -I "$DIR/gate.py"
elif xcode-select -p >/dev/null 2>&1; then
  exec /usr/bin/python3 -I "$DIR/gate.py"
fi
# Still installing and no usable Python yet: fail closed with a clear message.
cat >/dev/null
if [ "${SKILLSPECTOR_GATE_EVENT:-}" = "expansion" ]; then
  echo '{"decision":"block","reason":"Salience Guard is still installing SkillSpector (one time, ~5-10 min). Try again after the Salience Guard notification."}'
else
  echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Salience Guard is still installing SkillSpector (one time, ~5-10 min). Try again after the Salience Guard notification."}}'
fi
