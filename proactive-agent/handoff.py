"""delegate_task gate. A brief that is too short goes back to the main agent once per turn, and so
does one that names a file another running subagent is changing. Every accepted brief gets the
user's own words, memory, the chat's task ledger, working rules and a result schema. The rules and
the schema's needs_decision status are how a subagent, which cannot ask the user, brings a
question back to the main agent instead of guessing or working around a refusal."""

from __future__ import annotations

import copy
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config, ledger, reports, util, workers

BRIEFING_MARKER = "=== Handoff briefing (attached by proactive-agent) ==="

DEFAULT_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": list(reports.REPORT_STATUSES)},
        "summary": {"type": "string", "description": "What was done and the verified end state."},
        "evidence": {"type": "array", "items": {"type": "string"},
                     "description": "Checkable proof: URL + status code, command + exit code, file path, service state."},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "question": {"type": "string",
                     "description": "With needs_decision: the one question to decide, with the facts needed to answer it."},
        "options": {"type": "array", "items": {"type": "string"},
                    "description": "With needs_decision: the choices, each with what it would mean."},
    },
    "required": ["status", "summary"],
    # Hermes validates the final answer against this schema, so a needs_decision report without a
    # question gets a correction turn.
    "if": {"properties": {"status": {"const": reports.DECISION}}, "required": ["status"]},
    "then": {"required": ["question"]},
}

_RULES = (
    "- Do only the goal above. Never delete, stub out or rewrite working code or config just to make a check pass.\n"
    "- If a step fails the same way twice, stop trying variations and report the blocker.\n"
    "- You cannot ask the user. If the goal needs a choice that is not yours to make (between workable approaches, a "
    "production change, spending, deleting data) and nothing above settles it, stop and report status "
    "\"needs_decision\" with the question and the options. Do not guess.\n"
    "- If approval or a rule refuses a command (a BLOCKED result, a hook, an approval still pending), do not retry it "
    "or reach the same result another way. If the goal needs it, report \"needs_decision\": what was refused and why "
    "it is needed. A refusal that says the user denied it may be automatic.\n"
    "- Risky changes (SSH, firewall, DNS, deletes) only with a rollback path; otherwise report \"needs_decision\".\n"
    "- Do not edit a file another running subagent is changing; report it as a blocker instead.\n"
    "- Before reporting completed, check the real end state and list it as evidence."
)

_lock = threading.Lock()
_returned_turn: Dict[str, str] = {}
_clash_turn: Dict[str, str] = {}


def forget(session_id: str) -> None:
    with _lock:
        _returned_turn.pop(session_id, None)
        _clash_turn.pop(session_id, None)


def reset_all() -> None:
    with _lock:
        _returned_turn.clear()
        _clash_turn.clear()


def _memory_text() -> str:
    # Hermes already caps both files (memory_char_limit / user_char_limit), so they go in whole.
    try:
        from hermes_constants import get_hermes_home
        base = Path(get_hermes_home()) / "memories"
    except Exception:
        return ""
    parts = []
    for name, label in (("USER.md", "About the user"), ("MEMORY.md", "About the environment")):
        try:
            text = (base / name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            parts.append(f"### {label}\n{text}")
    return "\n\n".join(parts)


def _running_section(running: List[dict]) -> str:
    rows = []
    for t in running[:5]:
        files = t.get("files") or []
        changed = f"; changing {', '.join(files[:8])}" if files else ""
        rows.append(f"- \"{util.short(t.get('goal'), 100)}\" (running {ledger.minutes_since(t.get('started'))} min"
                    f"{changed})")
    return "## Other subagents running now\n" + "\n".join(rows) if rows else ""


def _unfinished_section(parent: str) -> str:
    rows = []
    tasks = ledger.unfinished(parent)
    for t in tasks:
        row = [f"- \"{util.short(t.get('goal'), 100)}\" ended {t['status']} "
               f"{ledger.minutes_since(t.get('ended'))} min ago."]
        if t.get("summary"):
            row.append(f"  Summary: {util.short(t['summary'], 300)}")
        if t.get("blockers"):
            row.append("  Blockers: " + "; ".join(util.short(b, 150) for b in t["blockers"][:3]))
        if t.get("question"):
            row.append("  It asked: " + reports.describe_question(t["question"], t.get("options") or []))
        if t.get("steers"):
            row.append("  Corrections it got: " + "; ".join(util.short(s, 150) for s in t["steers"][-3:]))
        if t.get("files"):
            row.append("  Files it changed: " + ", ".join(t["files"][:8]))
        if t.get("transcript"):
            row.append(f"  Full log: {t['transcript']}")
        rows.append("\n".join(row))
    if not rows:
        return ""
    asked = any(t.get("question") for t in tasks)
    return ("## Earlier unfinished work in this chat (newest first)\n" + "\n".join(rows)
            + "\nIf your goal continues one of these, start from what it found instead of redoing it."
            + (" For a task that asked a question, its answer is in your goal and context or in the user's messages."
               if asked else ""))


def _briefing(user_words: List[str], parent: str, running: List[dict]) -> str:
    parts = [BRIEFING_MARKER]
    if user_words:
        parts.append("## The user's recent messages (latest last)\n"
                     + "\n".join(f"- {util.short(w, 600)}" for w in user_words))
    memory = _memory_text()
    if memory:
        parts.append("## User and environment memory\n" + memory)
    for section in (_running_section(running), _unfinished_section(parent)):
        if section:
            parts.append(section)
    parts.append("## Working rules\n" + _RULES)
    return "\n\n".join(parts)


def _mentions(text: str, path: str) -> bool:
    """Whether *text* names *path*: the full path, or its last two components (``app/x.toml``),
    not as part of a longer name (``myapp/x.toml``, ``x.toml.bak``). A sentence-ending period is fine."""
    path = path.strip().replace("\\", "/").rstrip("/")
    if path.startswith("./"):
        path = path[2:]
    if not path:
        return False
    # "./x" and "x" name the same file; drop the prefix so a one-segment path still matches.
    norm = re.sub(r"(?<![\w.])\./", "", text.replace("\\", "/"))
    candidates = [(path, r"[\w.\-/]")]
    tail = "/".join(path.split("/")[-2:])
    if "/" in tail:
        candidates.append((tail, r"[\w.\-]"))
    for needle, before in candidates:
        if re.search(rf"(?<!{before}){re.escape(needle)}(?![\w\-/]|\.\w)", norm):
            return True
    return False


def _clash(tasks: List[dict], running: List[dict]) -> Optional[tuple]:
    for t in tasks:
        text = f"{t.get('goal') or ''}\n{t.get('context') or ''}"
        for other in running:
            hits = [f for f in other.get("files") or [] if _mentions(text, f)]
            if hits:
                return other, hits
    return None


def _return_message(goal: str) -> str:
    return (
        "[proactive-agent] Handoff returned once: the subagent starts with no memory and cannot see this chat, "
        f"so the context for \"{util.short(goal, 80)}\" is too thin. Rewrite it with what is already known "
        "(findings, exact errors, what was tried), the exact targets (paths, URLs, hosts), what must not change, "
        "when to stop, and what evidence proves success. The user's recent messages, memory and working rules are "
        "attached automatically. Then call delegate_task again."
    )


def _clash_message(other: dict, files: List[str]) -> str:
    return (
        f"[proactive-agent] Handoff returned once: the running subagent \"{util.short(other.get('goal'), 80)}\" is "
        f"already changing {', '.join(files[:5])}. Two subagents editing the same files overwrite each other. Wait "
        "for it, redirect it with delegate_task(action='steer'), or stop it (action='stop'). Call delegate_task "
        "again to dispatch anyway."
    )


def _first_this_turn(seen: Dict[str, str], session_id: str, turn_id: str) -> bool:
    with _lock:
        first = seen.get(session_id) != turn_id
        seen[session_id] = turn_id
    return first


def gate(session_id: str, turn_id: str, args: Optional[dict], user_words: List[str]) -> Optional[dict]:
    args = args or {}
    action = str(args.get("action") or "spawn").lower()
    if action == "stop":
        workers.mark_stopped_by_parent(str(args.get("subagent_id") or ""), session_id)
        return None
    if action == "steer":
        if args.get("subagent_id") and args.get("message"):
            ledger.add_steer(str(args["subagent_id"]), str(args["message"]))
        return None
    if action != "spawn":
        return None

    single_goal = not isinstance(args.get("tasks"), list)
    if single_goal:
        tasks = [{"goal": args.get("goal"), "context": args.get("context"),
                  "output_schema": args.get("output_schema")}] if args.get("goal") else []
    else:
        tasks = [dict(t) for t in args["tasks"] if isinstance(t, dict)]
    if not tasks or any(BRIEFING_MARKER in str(t.get("context") or "") for t in tasks):
        return None

    min_chars = int(config.get("handoff_min_context_chars"))
    thin = [t for t in tasks if len(str(t.get("context") or "").strip()) < min_chars]
    if thin and _first_this_turn(_returned_turn, session_id, turn_id):
        return util.block(_return_message(str(thin[0].get("goal") or "")))

    running = ledger.running(session_id)
    clash = _clash(tasks, running)
    if clash and _first_this_turn(_clash_turn, session_id, turn_id):
        return util.block(_clash_message(*clash))

    briefing = _briefing(user_words, session_id, running)
    # A new dispatch is the follow-up an open question waits for; stop reminding of it.
    ledger.stop_awaiting(t["id"] for t in ledger.awaiting_decision(session_id))
    for t in tasks:
        own = str(t.get("context") or "").strip()
        t["context"] = f"{own}\n\n{briefing}" if own else briefing
        if not t.get("output_schema"):
            t["output_schema"] = copy.deepcopy(DEFAULT_OUTPUT_SCHEMA)

    if single_goal:
        return {"action": "modify", "args": {"context": tasks[0]["context"], "output_schema": tasks[0]["output_schema"]}}
    return {"action": "modify", "args": {"tasks": tasks}}
