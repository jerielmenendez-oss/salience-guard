---
description: Approve a skill that Salience Guard blocked, after you have reviewed it yourself
argument-hint: <skill name or folder path>
disable-model-invocation: true
---
I want to approve this blocked skill or agent: $ARGUMENTS

1. Find its folder (a directory containing SKILL.md, or an agent .md file) on this Mac. If more than one matches, list them and ask me which one.
2. Show me the SkillSpector findings for it from `~/.skillspector-watch/ALERTS.md` and a short plain-language summary of what the skill does, then ask me to confirm. Do not approve without my explicit yes in this conversation.
3. After I confirm, run:
   `~/.local/share/uv/tools/skillspector/bin/python -I ~/.skillspector-watch/watch.py --approve "<full path>"`
   The approval is tied to the skill's current content; if it is edited later it gets blocked and scanned again.
