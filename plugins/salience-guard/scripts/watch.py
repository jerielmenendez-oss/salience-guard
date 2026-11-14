#!/usr/bin/env python3
"""SkillSpector watcher for the Salience Media Claude ecosystem on this Mac.

Run by launchd (com.salience.skillspector-watch). Each run:
  1. discovers every skill (folder with SKILL.md) and every Claude agent/command
     (.md in a .claude/agents or .claude/commands folder) under the watched roots;
  2. fingerprints each one; anything new or changed gets scanned by SkillSpector
     (static + Claude LLM analysis via the local `claude` login);
  3. sends a macOS notification and appends to ALERTS.md / watch.log.

First run (no state.json) is a silent static-only baseline of everything present.
Commands:  watch.py            normal run
           watch.py --status   print current inventory summary
           watch.py --rescan PATH   force rescan of one skill
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HOME = Path.home()
BASE = HOME / ".skillspector-watch"
STATE = BASE / "state.json"
REPORTS = BASE / "reports"
LOG = BASE / "watch.log"
ALERTS = BASE / "ALERTS.md"
LOCK = BASE / ".lock"
SKILLSPECTOR = HOME / ".local/bin/skillspector"

# Where Claude skills/agents can appear on this Mac.
VAULT_CANDIDATES = [  # shared partner vault (Obsidian Sync); wherever each partner keeps it
    HOME / "Documents/Salience HQ",
    HOME / "Salience HQ",
    HOME / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Salience HQ",
]
FIXED_ROOTS = [HOME / ".claude"] + VAULT_CANDIDATES
# Plus every project-level .claude folder up to 3 levels under home (found each run).
PROJECT_SEARCH_DEPTH = 3

SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__", ".trash", ".Trash",
    "Library", "projects",  # ~/.claude/projects = transcripts/memory, not skills
    "shell-snapshots", "todos", "statsig", "file-history", "debug", "ide",
}
SKIP_PATHS = {
    HOME / "SkillSpector",          # scanner repo itself (contains malicious test fixtures)
    HOME / ".skillspector-watch",
    HOME / ".hermes",               # Hermes agent, not part of the Claude ecosystem
}

LLM_TIMEOUT = 600
STATIC_TIMEOUT = 180
MAX_FILE_HASH_BYTES = 20 * 1024 * 1024


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    with LOG.open("a") as f:
        f.write(line + "\n")
    if sys.stdout.isatty():
        print(line)


def notify(title: str, message: str, sound: str | None = None) -> None:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')[:230]
    script = f'display notification "{esc(message)}" with title "{esc(title)}"'
    if sound:
        script += f' sound name "{sound}"'
    subprocess.run(["osascript", "-e", script], capture_output=True)


def skipped(p: Path) -> bool:
    return any(p == s or s in p.parents for s in SKIP_PATHS)


def project_claude_dirs() -> list[Path]:
    found = []
    def walk(d: Path, depth: int) -> None:
        if depth > PROJECT_SEARCH_DEPTH or skipped(d):
            return
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            if not e.is_dir(follow_symlinks=False) or e.name in SKIP_DIRS:
                continue
            if e.name == ".claude" and Path(e.path) != HOME / ".claude":
                found.append(Path(e.path))
            elif not e.name.startswith(".") or depth == 0 and e.name in (".config",):
                walk(Path(e.path), depth + 1)
    walk(HOME, 0)
    return found


def discover() -> dict[str, dict]:
    """Return {realpath: {"kind": skill|agent, "display": path-as-found}}."""
    units: dict[str, dict] = {}
    roots = [r for r in FIXED_ROOTS if r.exists()] + project_claude_dirs()
    seen_dirs: set[str] = set()
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
            real = os.path.realpath(dirpath)
            parent = Path(dirpath)
            # agents/commands are checked even if the real folder was already walked
            # (roast-room's .claude/agents is a symlink into the HQ vault).
            if parent.name in ("agents", "commands") and parent.parent.name == ".claude":
                for fn in filenames:
                    if fn.endswith(".md"):
                        fp = os.path.realpath(os.path.join(dirpath, fn))
                        units.setdefault(fp, {"kind": "agent", "display": os.path.join(dirpath, fn)})
            if real in seen_dirs or skipped(parent):
                dirnames[:] = []
                continue
            seen_dirs.add(real)
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            if "SKILL.md" in filenames:
                units.setdefault(real, {"kind": "skill", "display": dirpath})
    # Skills the gate saw being used outside the watched folders (e.g. a deep git clone).
    qf = BASE / "queue.json"
    if qf.exists():
        try:
            q = json.loads(qf.read_text())
        except ValueError:
            q = {}
        keep = {}
        for p, kind in q.items():
            ok = Path(p, "SKILL.md").is_file() if kind == "skill" else Path(p).is_file()
            if ok:
                keep[p] = kind
                units.setdefault(p, {"kind": kind, "display": p})
        if keep != q:
            qf.write_text(json.dumps(keep, indent=1))
    return units


def fingerprint(real: str, kind: str) -> str:
    h = hashlib.sha256()
    p = Path(real)
    files = [p] if kind == "agent" else sorted(
        f for f in p.rglob("*") if f.is_file() and not any(s in f.parts for s in SKIP_DIRS)
    )
    for f in files:
        try:
            if f.stat().st_size > MAX_FILE_HASH_BYTES:
                h.update(f"{f}:{f.stat().st_size}".encode())
                continue
            h.update(str(f.relative_to(p) if kind == "skill" else f.name).encode())
            h.update(f.read_bytes())
        except OSError:
            continue
    return h.hexdigest()


def scan(real: str, name: str, use_llm: bool) -> dict:
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:60]
    out = REPORTS / f"{safe}-{stamp}.json"
    env = dict(os.environ)
    env["PATH"] = f"{HOME}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
    cmd = [str(SKILLSPECTOR), "scan", real, "--format", "json", "--output", str(out)]
    if use_llm:
        env["SKILLSPECTOR_PROVIDER"] = "claude_cli"
    else:
        cmd.append("--no-llm")
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True,
                           timeout=LLM_TIMEOUT if use_llm else STATIC_TIMEOUT, cwd=str(BASE))
    except subprocess.TimeoutExpired:
        if use_llm:
            log(f"LLM scan timed out for {real}; falling back to static")
            return scan(real, name, False)
        return {"error": "timeout"}
    if not out.exists():
        if use_llm:
            log(f"LLM scan failed for {real} ({r.stderr.strip()[-300:]}); falling back to static")
            return scan(real, name, False)
        return {"error": (r.stderr or r.stdout).strip()[-500:]}
    d = json.loads(out.read_text())
    ra = d.get("risk_assessment", {})
    issues = d.get("issues", [])
    return {
        "score": ra.get("score"),
        "severity": ra.get("severity"),
        "recommendation": ra.get("recommendation"),
        "issues": len(issues),
        "top_issues": [
            f"[{i.get('severity','?')}] {i.get('category','')}: {i.get('finding') or i.get('pattern') or ''} "
            f"({(i.get('location') or {}).get('file','') if isinstance(i.get('location'), dict) else i.get('location','')})".strip()[:220]
            for i in issues[:8]
        ],
        "llm": bool(d.get("metadata", {}).get("llm_available")) and use_llm,
        "report": str(out),
    }


def unit_name(info: dict) -> str:
    p = Path(info["display"])
    return p.stem if info["kind"] == "agent" else p.name


def is_risky(res: dict) -> bool:
    return res.get("recommendation") in ("CAUTION", "DO_NOT_INSTALL") or "error" in res


def record_alert(event: str, name: str, info: dict, res: dict) -> None:
    lines = [
        f"\n## {datetime.now():%Y-%m-%d %H:%M} — {event}: {name}",
        f"- Path: `{info['display']}`",
    ]
    if "error" in res:
        lines.append(f"- Scan ERROR: {res['error']}")
    else:
        lines.append(f"- Verdict: **{res['recommendation']}** (risk {res['score']}/100, {res['severity']}, "
                     f"{res['issues']} issues, LLM {'yes' if res['llm'] else 'no'})")
        lines += [f"  - {t}" for t in res["top_issues"]]
        lines.append(f"- Full report: `{res['report']}`")
    if not ALERTS.exists():
        ALERTS.write_text("# SkillSpector alerts — Salience Media Claude ecosystem\n")
    with ALERTS.open("a") as f:
        f.write("\n".join(lines) + "\n")


def run() -> None:
    BASE.mkdir(exist_ok=True)
    lock = LOCK.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return  # previous run still going
    first_run = not STATE.exists()
    state: dict = {} if first_run else json.loads(STATE.read_text())
    units = discover()

    todo = []
    for real, info in units.items():
        fp = fingerprint(real, info["kind"])
        prev = state.get(real)
        if prev and prev.get("hash") == fp:
            continue
        todo.append((real, info, fp, "NEW" if not prev else "CHANGED"))

    removed = [k for k in state if k not in units]
    for k in removed:
        log(f"removed: {state[k].get('display')}")
        state.pop(k)

    if not todo:
        if removed:
            STATE.write_text(json.dumps(state, indent=1))
        return

    use_llm = not first_run
    log(f"{'BASELINE' if first_run else 'run'}: {len(todo)} to scan (llm={use_llm})")

    def work(item):
        real, info, fp, event = item
        return item, scan(real, unit_name(info), use_llm)

    results = []
    with ThreadPoolExecutor(max_workers=4 if first_run else 2) as ex:
        for (real, info, fp, event), res in ex.map(work, todo):
            name = unit_name(info)
            state[real] = {"hash": fp, "kind": info["kind"], "display": info["display"],
                           "scanned_at": datetime.now().isoformat(timespec="seconds"),
                           **{k: v for k, v in res.items() if k != "top_issues"}}
            STATE.write_text(json.dumps(state, indent=1))
            verdict = res.get("recommendation", "ERROR")
            log(f"{event} {info['kind']} {name}: {verdict} score={res.get('score')} -> {info['display']}")
            if not first_run or is_risky(res):
                record_alert("Baseline flag" if first_run else event, name, info, res)
            results.append((event, name, info, res))

    risky = [r for r in results if is_risky(r[3])]
    if first_run:
        notify("SkillSpector baseline done",
               f"{len(results)} skills/agents scanned. {len(risky)} need review — see ~/.skillspector-watch/ALERTS.md",
               "Glass" if risky else None)
        return

    for event, name, info, res in risky:
        notify(f"⚠️ Risky Claude skill: {name}",
               f"{event} {info['kind']} — {res.get('recommendation','SCAN ERROR')} "
               f"(risk {res.get('score','?')}/100). Details in ALERTS.md", "Basso")
    safe = [r for r in results if not is_risky(r[3])]
    if len(safe) > 3:
        notify("SkillSpector", f"{len(safe)} new/updated skills scanned — all SAFE")
    else:
        for event, name, info, res in safe:
            notify(f"New Claude {info['kind']}: {name}",
                   f"{event} — SAFE (risk {res.get('score')}/100)")


def status() -> None:
    if not STATE.exists():
        print("No baseline yet.")
        return
    state = json.loads(STATE.read_text())
    by = {}
    for v in state.values():
        by.setdefault(v.get("recommendation", "ERROR"), []).append(v)
    print(f"{len(state)} skills/agents tracked")
    for k, vs in sorted(by.items()):
        print(f"  {k}: {len(vs)}")
    for k in ("DO_NOT_INSTALL", "CAUTION", "ERROR"):
        for v in sorted(by.get(k, []), key=lambda v: -(v.get("score") or 0)):
            print(f"  [{k}] {v.get('score')} {v['display']}")


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    elif "--approve" in sys.argv:
        p = os.path.realpath(sys.argv[sys.argv.index("--approve") + 1])
        kind = "agent" if p.endswith(".md") else "skill"
        af = BASE / "approved.json"
        ap = json.loads(af.read_text()) if af.exists() else {}
        ap[p] = fingerprint(p, kind)
        af.write_text(json.dumps(ap, indent=1))
        log(f"APPROVED by user: {p}")
        print(f"Approved (pinned to current content): {p}")
    elif "--rescan" in sys.argv:
        p = os.path.realpath(sys.argv[sys.argv.index("--rescan") + 1])
        st = json.loads(STATE.read_text()) if STATE.exists() else {}
        st.pop(p, None)
        STATE.write_text(json.dumps(st, indent=1))
        run()
    else:
        run()
