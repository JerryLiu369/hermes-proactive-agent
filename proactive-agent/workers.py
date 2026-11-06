"""Delegated workers: the registry of running subagents, the stop on repeated steps, and the note
when approval or a rule refuses a step.

A worker is governed only when the main agent that spawned it is governed. Its counters live
here, in memory; what the task is, which files it changed and how it ended go to the ledger.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

from . import config, ledger, util

logger = logging.getLogger("hermes.plugins.proactive_agent")

_lock = threading.RLock()
_workers: Dict[str, dict] = {}
_by_session: Dict[str, str] = {}

_STALE_AFTER_S = 6 * 3600.0
_DONE_KEEP_S = 600.0
# Substrings of tool names that change files (write_file, patch, MCP edit_file ...) and of those that only look.
_WRITE_HINTS = ("write", "edit", "patch")
_READ_HINTS = ("read", "search", "list", "view")


def is_write(tool_name: str) -> bool:
    name = (tool_name or "").lower()
    return any(h in name for h in _WRITE_HINTS) and not any(h in name for h in _READ_HINTS)


# ---------------------------------------------------------------- registry

def lookup(task_id: str = "", session_id: str = "") -> Optional[dict]:
    with _lock:
        if task_id and task_id in _workers:
            return _workers[task_id]
        wid = _by_session.get(session_id or "")
        return _workers.get(wid) if wid else None


def _drop(wid: str) -> None:
    _by_session.pop(_workers.pop(wid).get("session_id") or "", None)


def _prune(now: float) -> None:
    for wid in [wid for wid, w in _workers.items()
                if (w["done"] and now - w["last_activity"] > _DONE_KEEP_S) or now - w["spawn"] > _STALE_AFTER_S]:
        _drop(wid)


def on_subagent_start(is_parent_governed: Callable[[str], bool], **kw: Any) -> None:
    parent_session = kw.get("parent_session_id") or ""
    child_id = kw.get("child_subagent_id") or ""
    if not child_id:
        return
    with _lock:
        parent_worker = _workers.get(kw.get("parent_subagent_id") or "")
    if parent_worker is None and not is_parent_governed(parent_session):
        return
    # A subagent's own subagents report to the chat that started the chain.
    owner = parent_worker["parent_session"] if parent_worker else parent_session
    now = time.monotonic()
    worker = {
        "id": child_id,
        "session_id": kw.get("child_session_id") or "",
        "parent_session": owner,
        "goal": str(kw.get("child_goal") or ""),
        "spawn": now,
        "last_activity": now,
        "rounds": set(),
        "repeats": collections.Counter(),
        "pending_note": None,
        "stop_reason": None,
        "stop_noted": False,
        "refused": set(),
        "done": False,
        "stopped_by_parent": False,
    }
    with _lock:
        _prune(now)
        _workers[child_id] = worker
        if worker["session_id"]:
            _by_session[worker["session_id"]] = child_id
    ledger.start(child_id, owner, worker["goal"], worker["session_id"])


def mark_stopped_by_parent(subagent_id: str, parent_session: str) -> None:
    with _lock:
        w = _workers.get(subagent_id or "")
        if w is not None:
            w["stopped_by_parent"] = True
    ledger.mark_stopped(subagent_id, parent_session)


def finish(child_session: str) -> Optional[dict]:
    """Mark the worker behind *child_session* done and return it; None when it is unknown here."""
    with _lock:
        w = lookup(session_id=child_session) if child_session else None
        if w is not None:
            w["done"] = True
            w["last_activity"] = time.monotonic()
        return w


def forget_parent(parent_session: str) -> None:
    """Drop in-memory state for one chat. The ledger keeps its records and pending notes."""
    if not parent_session:
        return
    with _lock:
        for wid in [wid for wid, w in _workers.items() if w["parent_session"] == parent_session]:
            _drop(wid)


def reset_all() -> None:
    with _lock:
        _workers.clear()
        _by_session.clear()


# ---------------------------------------------------------------- refused steps

_REFUSED = ("[proactive-agent] {what} Do not retry it, rephrase it or reach the same result another way. If the goal "
            "needs it, stop now and report status \"needs_decision\": what was refused, why it is needed, and the "
            "options. The main agent decides or asks the user. A refusal that says the user denied it may be automatic.")


def _approval_refusal(result: Any) -> Optional[str]:
    """What Hermes' approval did to a call: refused it (every refusal is an error starting with
    BLOCKED), or left it waiting for an answer this run cannot wait for (terminal status
    "pending_approval", which carries no message of its own)."""
    data = util.json_object(result)
    if data is None:
        return None
    if data.get("status") == "pending_approval" or data.get("approval_pending"):
        return "This command did not run: it is waiting for the user's approval, and this run cannot wait for it."
    error = data.get("error")
    if isinstance(error, str) and error.lstrip().startswith("BLOCKED"):
        return "Hermes' approval or a safety rule refused this call, so it did not run."
    return None


def _first_refusal(w: dict, tool_name: str, args: Optional[dict]) -> bool:
    key = util.call_signature(tool_name, args)
    with _lock:
        if key in w["refused"]:
            return False
        w["refused"].add(key)
    return True


def _joined(*notes: Optional[str]) -> Optional[str]:
    return "\n".join(n for n in notes if n) or None


# ---------------------------------------------------------------- repeat stop

def _stop_message(w: dict) -> str:
    return (f"[proactive-agent] STOP: {w['stop_reason']}. Do not call more tools. Write your final report now: "
            "status \"blocked\", what you tried, the exact blocker, and what you need from the main agent or the user.")


def pre_tool_call(w: dict, tool_name: str, args: Optional[dict], **kw: Any) -> Optional[dict]:
    with _lock:
        w["last_activity"] = time.monotonic()
        round_id = kw.get("api_request_id") or kw.get("tool_call_id") or ""
        if round_id:
            w["rounds"].add(round_id)
        if w["stop_reason"]:
            w["stop_noted"] = True
            return util.block(_stop_message(w))
    return None


def post_tool_call(w: dict, tool_name: str, args: Optional[dict], result: Any, **kw: Any) -> None:
    if kw.get("error_type") == "plugin_block":
        # A hook or plugin refused the call. Hermes runs no transform_tool_result for a blocked
        # call, so the note rides on the worker's next tool result. The plugin's own STOP is not news.
        message = str(kw.get("error_message") or "")
        if not message.startswith("[proactive-agent]") and _first_refusal(w, tool_name, args):
            note = _REFUSED.format(what=f"Your earlier call {util.describe_call(tool_name, args)} was refused by a hook "
                                        f"or plugin rule (\"{util.short(message, 160)}\") and did not run.")
            with _lock:
                w["pending_note"] = _joined(w["pending_note"], note)
        return
    args = args or {}
    if is_write(tool_name) and isinstance(args.get("path"), str) and kw.get("status") != "error":
        ledger.add_files(w["id"], [args["path"]])
    if w["stopped_by_parent"]:
        # The main agent already stopped it; its last in-flight calls are not a stuck loop.
        return
    limit = max(2, int(config.get("worker_repeat_limit")))
    warn_at = limit - 2 if limit >= 4 else 0
    outcome = util.result_signature(result)
    step = f"{util.describe_call(tool_name, args)} -> \"{util.short(outcome, 120)}\""
    stopped = None
    with _lock:
        w["last_activity"] = time.monotonic()
        key = (util.call_signature(tool_name, args), outcome)
        w["repeats"][key] += 1
        count = w["repeats"][key]
        if count >= limit and w["stop_reason"] is None:
            w["stop_reason"] = stopped = f"ran the same step {count} times with the same result: {step}"
        elif count == warn_at:
            w["pending_note"] = _joined(
                w["pending_note"], f"[proactive-agent] You ran the same step {count} times with the same result: {step}. "
                                   f"Change approach or report the blocker; at {limit} repeats you will be stopped.")
    if stopped:
        logger.warning("proactive-agent: stopping worker %s: %s", w["id"], stopped)


def transform_tool_result(w: dict, tool_name: str, args: Optional[dict], result: Any) -> Optional[str]:
    with _lock:
        if w["stop_reason"] and not w["stop_noted"]:
            w["stop_noted"] = True
            return util.append_note(result, _stop_message(w))
        note, w["pending_note"] = w["pending_note"], None
    refusal = _approval_refusal(result)
    if refusal and _first_refusal(w, tool_name, args):
        note = _joined(note, _REFUSED.format(what=refusal))
    return util.append_note(result, note) if note else None
