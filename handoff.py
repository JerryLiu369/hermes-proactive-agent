"""delegate_task gate: make every handoff carry what the main agent knows.

Thin or unsafe handoffs are returned once with what is missing. Accepted handoffs get the
user's memory, a summary of the conversation and the working rules appended automatically.
"""

from __future__ import annotations

import copy
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from . import config, guards, workers

logger = logging.getLogger("hermes.plugins.proactive_agent")

AUX_TASK = "proactive_handoff_summary"
BRIEFING_MARKER = "=== Handoff briefing (attached by proactive-agent) ==="

DEFAULT_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "partial", "blocked", "failed"]},
        "summary": {"type": "string", "description": "What was done and the verified end state."},
        "evidence": {"type": "array", "items": {"type": "string"},
                     "description": "Checkable proof: URL + status code, command + exit code, file path, service state."},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "files_changed": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "summary"],
}

_lock = threading.Lock()
_returned: Dict[str, Set[str]] = {}
_aux_task_ready = False


def mark_aux_task_ready() -> None:
    global _aux_task_ready
    _aux_task_ready = True


def forget(session_id: str = "") -> None:
    with _lock:
        if session_id:
            _returned.pop(session_id, None)
        else:
            _returned.clear()


def _once(session_id: str, key: str) -> bool:
    """True the first time a (session, key) pair is seen."""
    with _lock:
        seen = _returned.setdefault(session_id, set())
        if key in seen:
            return False
        seen.add(key)
        return True


# ---------------------------------------------------------------- briefing parts

def _memory_text() -> str:
    limit = int(config.get("handoff_memory_max_chars"))
    if limit <= 0:
        return ""
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
    joined = "\n\n".join(parts)
    return joined if len(joined) <= limit else joined[:limit] + "\n…(truncated)"


def _transcript(history: List[dict], trace: List[dict], cap: int = 20000) -> str:
    lines = []
    for m in history[-30:]:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        text = guards.short(m.get("text"), 1500)
        if text:
            lines.append(f"{role.upper()}: {text}")
    if trace:
        lines.append("TOOLS RUN BY THE MAIN AGENT THIS TURN:")
        for t in trace[-12:]:
            lines.append(f"- {t['call']} -> {guards.short(t['result'], 300)}")
    text = "\n".join(lines)
    return text[-cap:]


def _fallback_summary(history: List[dict], trace: List[dict]) -> str:
    users = [guards.short(m.get("text"), 300) for m in history if m.get("role") == "user" and m.get("text")][-4:]
    mains = [guards.short(m.get("text"), 400) for m in history if m.get("role") == "assistant" and m.get("text")][-2:]
    lines = []
    if users:
        lines.append("Recent user messages:")
        lines += [f"- {u}" for u in users]
    if mains:
        lines.append("Main agent's latest replies:")
        lines += [f"- {a}" for a in mains]
    if trace:
        lines.append("Checked by the main agent this turn:")
        lines += [f"- {t['call']} -> {guards.short(t['result'], 200)}" for t in trace[-8:]]
    return "\n".join(lines)


_SUMMARY_PROMPT = (
    "You write handoff briefs. A subagent with no memory and no view of this chat will do the task below. "
    "From the conversation, extract only what it needs, as short bullet points under these headings: "
    "User's intent; Facts already established; Already tried and the result; Must not change; Open questions. "
    "Quote exact paths, hosts, URLs, error messages and numbers. Do not invent anything. Max 300 words. "
    "Write in the same language the user writes in."
)


def _summary(goals: List[str], history: List[dict], trace: List[dict]) -> str:
    if not history and not trace:
        return ""
    ctx = config.ctx()
    llm = getattr(ctx, "llm", None) if ctx is not None else None
    if config.get("handoff_summary_enabled") and llm is not None:
        try:
            kwargs: Dict[str, Any] = dict(
                messages=[
                    {"role": "system", "content": _SUMMARY_PROMPT},
                    {"role": "user", "content": "TASK FOR THE SUBAGENT:\n" + "\n".join(f"- {g}" for g in goals)
                     + "\n\nCONVERSATION:\n" + _transcript(history, trace)},
                ],
                max_tokens=700,
                temperature=0.2,
                timeout=float(config.get("handoff_summary_timeout_seconds")),
                purpose="proactive-agent.handoff-summary",
            )
            if _aux_task_ready:
                kwargs["task"] = AUX_TASK
            result = llm.complete(**kwargs)
            text = str(getattr(result, "text", "") or "").strip()
            if text:
                return text
        except Exception as e:
            logger.info("proactive-agent: handoff summary fell back to plain extract: %s", e)
    return _fallback_summary(history, trace)


def _rules(lang: str, risky_ok: bool) -> str:
    rules = [
        "- Scope: do only the goal above. Never delete, stub out or rewrite working code or config just to make a check pass.",
        "- Fail fast: if a command or check fails the same way twice, stop retrying variations. Diagnose once, then report the blocker.",
        "- No unbounded searches (grep -r / find over home, /, or ~/.hermes) and no port scans. Read known paths directly.",
        "- Before reporting completed, check the real end state (public URL status code, service status, file on disk, exit code) and list it as evidence.",
        "- If you cannot finish, report status \"blocked\" with what you tried and the exact blocker. That is a good result.",
    ]
    if risky_ok:
        rules.append("- Risky changes are allowed only as described in this handoff. Keep the rollback path working until the new state is verified.")
    else:
        rules.append("- Risky changes (SSH/firewall, DNS, deletes, VM lifecycle) are not authorized for this task. If you need one, report it instead.")
    rules.append("- Write the final report in Chinese." if lang == "zh" else "- Write the final report in English.")
    return "\n".join(rules)


def _briefing(summary: str, memory: str, rules: str) -> str:
    parts = [BRIEFING_MARKER]
    if summary:
        parts.append("## What the user and the main agent already know\n" + summary)
    if memory:
        parts.append("## User and environment memory\n" + memory)
    parts.append("## Working rules\n" + rules)
    return "\n\n".join(parts)


# ---------------------------------------------------------------- gate

def _missing(goal: str, context: str) -> List[str]:
    missing = []
    if len(context.strip()) < int(config.get("handoff_min_context_chars")):
        missing.append("context")
    text = f"{goal}\n{context}"
    if guards.task_is_risky(text) and not guards.has_rollback(text):
        missing.append("rollback")
    return missing


def _return_message(goal: str, missing: List[str]) -> str:
    lines = [
        "[proactive-agent] Handoff returned once: the subagent starts with no memory and cannot see this chat, "
        f"so the brief for \"{guards.short(goal, 80)}\" is not enough.",
        "Rewrite `context` to cover:",
        "- What is already known: findings, exact errors seen, what was tried and what happened",
        "- Exact targets: paths, URLs, hosts, services",
        "- What must not be changed",
        "- When to stop and report back",
        "- What evidence proves success",
    ]
    if "rollback" in missing:
        lines.append("- Rollback path: how access or service stays recoverable if the change fails "
                     "(for example keep port 22 open until the new port is verified, or back up the record first)")
    lines.append("User memory, a chat summary and working rules are attached automatically; do not copy them. "
                 "Then call delegate_task again.")
    return "\n".join(lines)


def _pivot_message(conflicts: List[tuple]) -> str:
    rows = [f"- {w['id']}: \"{guards.short(w['goal'], 60)}\" (shared targets: {', '.join(hits[:3])})"
            for w, hits in conflicts]
    return (
        "[proactive-agent] A subagent is still running on the same targets:\n" + "\n".join(rows) + "\n"
        "If the new task replaces it, stop it first with delegate_task(action='stop', subagent_id=...) so a late "
        "result cannot overwrite the new work. If both should run, call delegate_task again unchanged."
    )


def gate(session_id: str, turn_id: str, args: Optional[dict], history: List[dict], trace: List[dict],
         lang: str) -> Optional[dict]:
    args = args or {}
    action = str(args.get("action") or "spawn").lower()
    if action == "stop":
        workers.mark_stopped_by_parent(str(args.get("subagent_id") or ""))
        return None
    if action != "spawn":
        return None

    legacy = not isinstance(args.get("tasks"), list)
    tasks = [dict(t) for t in args.get("tasks") or [] if isinstance(t, dict)] if not legacy else (
        [{"goal": args.get("goal"), "context": args.get("context"), "output_schema": args.get("output_schema")}]
        if args.get("goal") else [])
    if not tasks:
        return None
    if any(BRIEFING_MARKER in str(t.get("context") or "") for t in tasks):
        return None

    for t in tasks:
        goal, context = str(t.get("goal") or ""), str(t.get("context") or "")
        missing = _missing(goal, context)
        # One return per turn, so a reworded goal cannot bounce forever.
        if missing and _once(session_id, f"thin:{turn_id}"):
            return _block(_return_message(goal, missing))

    active = workers.active_for_parent(session_id)
    if active:
        conflicts = []
        new_targets = set()
        for t in tasks:
            new_targets |= guards.targets_in(f"{t.get('goal') or ''}\n{t.get('context') or ''}")
        for w in active:
            hits = guards.any_overlap(new_targets, w["targets"])
            if hits:
                conflicts.append((w, hits))
        if conflicts and _once(session_id, "pivot:" + ",".join(sorted(w["id"] for w, _ in conflicts))):
            return _block(_pivot_message(conflicts))

    summary = _summary([str(t.get("goal") or "") for t in tasks], history, trace)
    memory = _memory_text()
    handoffs = []
    for t in tasks:
        goal, context = str(t.get("goal") or ""), str(t.get("context") or "")
        text = f"{goal}\n{context}"
        risky_ok = guards.task_is_risky(text) and guards.has_rollback(text)
        t["context"] = (context.rstrip() + "\n\n" if context.strip() else "") + _briefing(summary, memory, _rules(lang, risky_ok))
        if not t.get("output_schema"):
            t["output_schema"] = copy.deepcopy(DEFAULT_OUTPUT_SCHEMA)
        handoffs.append({"goal": goal, "risky_ok": risky_ok, "targets": guards.targets_in(text), "lang": lang})
    workers.expect_children(session_id, handoffs)

    if legacy:
        return {"action": "modify", "args": {"context": tasks[0]["context"], "output_schema": tasks[0]["output_schema"]}}
    return {"action": "modify", "args": {"tasks": tasks}}


def _block(message: str) -> dict:
    return {"action": "block", "message": message, "reason": message}
