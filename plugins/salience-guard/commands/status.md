---
description: Show Salience Guard status - which skills are safe, flagged or blocked
disable-model-invocation: true
---
Run this and summarise the result for me in plain language (how many skills are safe, which are CAUTION or DO_NOT_INSTALL and why, and whether the background watcher is running):

```
~/.local/share/uv/tools/skillspector/bin/python -I ~/.skillspector-watch/watch.py --status; launchctl print gui/$(id -u)/com.salience.skillspector-watch | grep -E "state|last exit"; tail -40 ~/.skillspector-watch/ALERTS.md
```

If SkillSpector is not installed yet, show the last lines of `~/.skillspector-watch/install.log` instead.
