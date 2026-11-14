#!/usr/bin/env python3
"""Claude Code PreToolUse gate (Skill + Agent tools) backed by SkillSpector verdicts.

  SAFE / trusted / approved  -> allowed silently
  CAUTION                    -> "ask": you approve or reject in Claude Code
  DO_NOT_INSTALL             -> denied
  not scanned yet / changed  -> denied, and the watcher is kicked to scan it now

Verdicts come from ~/.skillspector-watch/state.json (written by watch.py).
Approve a flagged skill you have reviewed:  watch.py --approve <path>
(approval is pinned to the skill's exact content; any edit re-gates it).
Skills/agents not found on disk (Claude Code built-ins) are allowed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BASE = Path.home() / ".skillspector-watch"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import watch  # noqa: E402  (fingerprint + paths)

HOME = Path.home()
OFFICIAL_MARKETPLACE = HOME / ".claude/plugins/marketplaces/claude-plugins-official"
SYNCED = HOME / ".claude/skills/synced"
RANK = {"SAFE": 0, "CAUTION": 1, "PENDING": 2, "DO_NOT_INSTALL": 3}


def frontmatter_name(md: Path) -> str | None:
    try:
        with md.open(errors="ignore") as f:
            if f.readline().strip() != "---":
                return None
            for _ in range(40):
                line = f.readline()
                if not line or line.strip() == "---":
                    return None
                if line.startswith("name:"):
                    return line.split(":", 1)[1].strip().strip("'\"")
    except OSError:
        return None
    return None


def claude_dirs(cwd: Path) -> list[Path]:
    """Project .claude dirs from cwd up to home, then ~/.claude."""
    out, d = [], cwd
    while True:
        if (d / ".claude").is_dir() and d != HOME:
            out.append(d / ".claude")
        if d == d.parent or d == HOME:
            break
        d = d.parent
    out.append(HOME / ".claude")
    return out


def skill_candidates(name: str, cwd: Path) -> list[tuple[Path, str]]:
    prefix, _, base = name.rpartition(":")
    found: list[tuple[Path, str]] = []
    dirs = claude_dirs(cwd)
    if prefix and "/" in prefix:  # directory-scoped skill, e.g. apps/web:deploy
        dirs.insert(0, cwd / prefix / ".claude")
    skill_dirs = [d / "skills" for d in dirs] + [p for p in SYNCED.glob("*") if p.is_dir()]
    for sd in skill_dirs:
        if not sd.is_dir():
            continue
        for p in sd.iterdir():
            if (p / "SKILL.md").is_file() and (p.name == base or frontmatter_name(p / "SKILL.md") == base):
                found.append((p, "skill"))
    for d in dirs:
        f = d / "commands" / f"{base}.md"
        if f.is_file():
            found.append((f, "agent"))
    if prefix and "/" not in prefix:  # plugin:skill
        plug_root = HOME / ".claude/plugins"
        for p in plug_root.glob(f"**/{prefix}/skills/{base}"):
            if (p / "SKILL.md").is_file():
                found.append((p, "skill"))
        for p in plug_root.glob(f"**/{prefix}/commands/{base}.md"):
            found.append((p, "agent"))
    return found


def agent_candidates(name: str, cwd: Path) -> list[tuple[Path, str]]:
    base = name.rpartition(":")[2]
    found = []
    for d in claude_dirs(cwd):
        ad = d / "agents"
        if not ad.is_dir():
            continue
        for f in ad.glob("*.md"):
            if f.stem == base or frontmatter_name(f) == base:
                found.append((f, "agent"))
    if ":" in name:
        prefix = name.split(":")[0]
        for f in (HOME / ".claude/plugins").glob(f"**/{prefix}/agents/{base}.md"):
            found.append((f, "agent"))
    return found


SELF_ROOT = Path(__file__).resolve().parent.parent  # this plugin's own folder


def trusted(real: str) -> bool:
    p = Path(real)
    if OFFICIAL_MARKETPLACE in p.parents or SELF_ROOT in p.parents:
        return True
    if SYNCED in p.parents:  # claude.ai-synced: trust only Anthropic-provided ones
        manifest = p.parent / "manifest.json"
        try:
            for s in json.loads(manifest.read_text()).get("skills", []):
                if s.get("name") == p.name or s.get("skillId") == p.name:
                    return s.get("source") in ("anthropic", "anthropic-example")
        except (OSError, ValueError):
            return False
    return False


def verdict(path: Path, kind: str, state: dict, approved: dict) -> tuple[str, str, dict]:
    real = os.path.realpath(path)
    if trusted(real):
        return "SAFE", real, {}
    fp = watch.fingerprint(real, kind)
    if approved.get(real) == fp:
        return "SAFE", real, {}
    rec = state.get(real)
    if not rec or rec.get("hash") != fp:
        return "PENDING", real, {}
    v = rec.get("recommendation") or "PENDING"
    return v, real, rec


def queue_for_scan(real: str, kind: str) -> None:
    """Tell the watcher about a skill outside its usual folders (e.g. a deep git clone)."""
    qf = BASE / "queue.json"
    try:
        q = json.loads(qf.read_text()) if qf.exists() else {}
    except ValueError:
        q = {}
    q[real] = kind
    qf.write_text(json.dumps(q, indent=1))


def kick_watcher() -> None:
    subprocess.Popen(["launchctl", "kickstart", f"gui/{os.getuid()}/com.salience.skillspector-watch"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def top_findings(rec: dict) -> str:
    try:
        d = json.loads(Path(rec["report"]).read_text())
        items = [f"- [{i.get('severity')}] {i.get('category')}: {str(i.get('finding'))[:140]}" for i in d.get("issues", [])[:5]]
        return "\n".join(items)
    except Exception:
        return ""


def out(decision: str, reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))


def block_prompt(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))


def expansion(data: dict) -> None:
    """User typed /name: gate the skill/command before it expands."""
    name = data.get("command_name") or ""
    cwd = Path(data.get("cwd") or os.getcwd())
    cands = skill_candidates(name, cwd) if name else []
    if not cands:
        return
    state = json.loads(watch.STATE.read_text()) if watch.STATE.exists() else {}
    approved_f = BASE / "approved.json"
    approved = json.loads(approved_f.read_text()) if approved_f.exists() else {}
    worst = ("SAFE", "", {})
    for path, kind in cands:
        v = verdict(path, kind, state, approved)
        if RANK.get(v[0], 2) > RANK.get(worst[0], 2):
            worst = v
    v, real, rec = worst
    if v == "PENDING":
        queue_for_scan(real, "agent" if real.endswith(".md") else "skill")
        kick_watcher()
        block_prompt(f"SkillSpector gate: /{name} is new or changed and has not been scanned yet. "
                     f"A scan has started (~1-2 min). Run it again after the notification.")
    elif v == "DO_NOT_INSTALL":
        block_prompt(f"SkillSpector gate: /{name} was rated DO NOT INSTALL (risk {rec.get('score')}/100). Blocked.\n"
                     f"{top_findings(rec)}\nIf you reviewed and trust it:\n"
                     f"~/.local/share/uv/tools/skillspector/bin/python ~/.skillspector-watch/watch.py --approve '{real}'")
    elif v == "CAUTION":
        # You typed it yourself, so it runs, but with a visible warning.
        print(json.dumps({"systemMessage": f"⚠️ SkillSpector: /{name} is rated CAUTION "
                                           f"(risk {rec.get('score')}/100). Details: ~/.skillspector-watch/ALERTS.md"}))


def main() -> None:
    data = json.load(sys.stdin)
    if data.get("hook_event_name") == "UserPromptExpansion":
        return expansion(data)
    tool = data.get("tool_name")
    ti = data.get("tool_input") or {}
    cwd = Path(data.get("cwd") or os.getcwd())
    if tool == "Skill":
        name = ti.get("skill") or ti.get("name") or ""
        cands = skill_candidates(name, cwd)
        label = "skill"
    elif tool == "Agent":
        name = ti.get("subagent_type") or ""
        cands = agent_candidates(name, cwd) if name else []
        label = "agent"
    else:
        return
    if not cands:
        return  # built-in: allow

    state = json.loads(watch.STATE.read_text()) if watch.STATE.exists() else {}
    approved_f = BASE / "approved.json"
    approved = json.loads(approved_f.read_text()) if approved_f.exists() else {}

    worst = ("SAFE", "", {})
    for path, kind in cands:
        v = verdict(path, kind, state, approved)
        if RANK.get(v[0], 2) > RANK.get(worst[0], 2):
            worst = v
    v, real, rec = worst
    if v == "SAFE":
        return
    if v == "PENDING":
        queue_for_scan(real, "agent" if real.endswith(".md") else "skill")
        kick_watcher()
        out("deny", f"SkillSpector gate: {label} '{name}' is new or changed and has not been scanned yet "
                    f"({real}). A scan has started; it takes ~1-2 minutes. Try again after the "
                    f"notification arrives.")
    elif v == "DO_NOT_INSTALL":
        out("deny", f"SkillSpector gate: {label} '{name}' was rated DO NOT INSTALL "
                    f"(risk {rec.get('score')}/100). Blocked.\n{top_findings(rec)}\n"
                    f"Path: {real}\nIf you reviewed it and trust it, run:\n"
                    f"~/.local/share/uv/tools/skillspector/bin/python ~/.skillspector-watch/watch.py --approve '{real}'")
    else:  # CAUTION
        out("ask", f"SkillSpector: {label} '{name}' rated CAUTION (risk {rec.get('score')}/100).\n"
                   f"{top_findings(rec)}\nAllow it to run?")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never silently allow on a crash
        msg = f"SkillSpector gate error ({type(e).__name__}: {e})."
        if '"UserPromptExpansion"' in sys.argv[1:] or os.environ.get("SKILLSPECTOR_GATE_EVENT") == "expansion":
            block_prompt(msg + " Blocked to be safe; check ~/.skillspector-watch/gate.py.")
        else:
            out("ask", msg + " Allow this anyway?")
