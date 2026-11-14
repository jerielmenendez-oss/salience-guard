# Salience Guard

A Claude Code plugin for the Salience Media team. Before any Claude **skill, agent or slash command** can run, it has to pass a security scan by [NVIDIA SkillSpector](https://github.com/NVIDIA/SkillSpector).

| Scan result | Claude uses it on its own | You type `/name` |
|---|---|---|
| ⏳ New or edited, not scanned yet | Blocked; scan starts | Blocked; scan starts |
| ✅ SAFE | Runs | Runs |
| ⚠️ CAUTION | Asks you first | Runs with a warning |
| ⛔ DO NOT INSTALL | Blocked | Blocked |

Anthropic's own skills and Claude Code's built-ins are trusted and not blocked.

## Install (Mac, once per person)

In Claude Code, in VS Code or in a terminal:

```
/plugin marketplace add jerielmenendez-oss/salience-guard
/plugin install salience-guard@salience
```

Then close and reopen Claude Code. On the first session it installs SkillSpector in the background (5–10 minutes, one time) and scans the skills you already have. You get a Mac notification when it's ready.

## Day to day

- You get a Mac notification for every new skill and its verdict.
- `/salience-guard:status` shows what's safe, flagged or blocked.
- `/salience-guard:approve <skill>` lets through a blocked skill **after you have reviewed it**. The approval only covers the skill exactly as it is; any later edit gets it scanned again.
- Full alert history: `~/.skillspector-watch/ALERTS.md`

## What it watches

`~/.claude` (your skills, plugins, agents, commands), every project's `.claude` folder, and the Salience HQ vault. A skill anywhere else gets scanned the first time Claude tries to use it.

## Notes

- Mac only.
- The deeper AI part of the scan uses your own Claude login through the `claude` command. If `claude` isn't installed in the terminal, it falls back to the quick static scan only.
- SkillSpector is strict, so a CAUTION or DO NOT INSTALL verdict means "review this", not "this is malware".
