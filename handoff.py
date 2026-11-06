"""delegate_task gate. A brief that is too short goes back to the main agent once per turn;
every accepted brief gets the user's own words, memory, working rules and a result schema."""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config, util, workers

BRIEFING_MARKER = "=== Handoff briefing (attached by proactive-agent) ==="

DEFAULT_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "partial", "blocked", "failed"]},
        "summary": {"type": "string", "description": "What was done and the verified end state."},
        "evidence": {"type": "array", "items": {"type": "string"},
                     "description": "Checkable proof: URL + status code, command + exit code, file path, service state."},
        "blockers": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "summary"],
}

_RULES = (
    "- Do only the goal above. Never delete, stub out or rewrite working code or config just to make a check pass.\n"
    "- If a step fails the same way twice, stop trying variations and report the blocker.\n"
    "- Risky changes (SSH, firewall, DNS, deletes) only with a rollback path; otherwise report what you need.\n"
    "- Before reporting completed, check the real end state and list it as evidence."
)

_lock = threading.Lock()
_returned_turn: Dict[str, str] = {}


def forget(session_id: str = "") -> None:
    with _lock:
        if session_id:
            _returned_turn.pop(session_id, None)
        else:
            _returned_turn.clear()


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


def _briefing(user_words: List[str]) -> str:
    parts = [BRIEFING_MARKER]
    if user_words:
        parts.append("## The user's recent messages (latest last)\n"
                     + "\n".join(f"- {util.short(w, 600)}" for w in user_words))
    memory = _memory_text()
    if memory:
        parts.append("## User and environment memory\n" + memory)
    parts.append("## Working rules\n" + _RULES)
    return "\n\n".join(parts)


def _return_message(goal: str) -> str:
    return (
        "[proactive-agent] Handoff returned once: the subagent starts with no memory and cannot see this chat, "
        f"so the context for \"{util.short(goal, 80)}\" is too thin. Rewrite it with what is already known "
        "(findings, exact errors, what was tried), the exact targets (paths, URLs, hosts), what must not change, "
        "when to stop, and what evidence proves success. The user's recent messages, memory and working rules are "
        "attached automatically. Then call delegate_task again."
    )


def _block(message: str) -> dict:
    return {"action": "block", "message": message, "reason": message}


def gate(session_id: str, turn_id: str, args: Optional[dict], user_words: List[str]) -> Optional[dict]:
    args = args or {}
    action = str(args.get("action") or "spawn").lower()
    if action == "stop":
        workers.mark_stopped_by_parent(str(args.get("subagent_id") or ""))
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
    if thin:
        with _lock:
            first = _returned_turn.get(session_id) != turn_id
            _returned_turn[session_id] = turn_id
        if first:
            return _block(_return_message(str(thin[0].get("goal") or "")))

    briefing = _briefing(user_words)
    for t in tasks:
        own = str(t.get("context") or "").strip()
        t["context"] = f"{own}\n\n{briefing}" if own else briefing
        if not t.get("output_schema"):
            t["output_schema"] = copy.deepcopy(DEFAULT_OUTPUT_SCHEMA)

    if single_goal:
        return {"action": "modify", "args": {"context": tasks[0]["context"], "output_schema": tasks[0]["output_schema"]}}
    return {"action": "modify", "args": {"tasks": tasks}}
