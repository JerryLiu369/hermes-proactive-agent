"""Delegated workers: stop on repeated steps, result notes and the stuck alert.

A worker is governed only when the main agent that spawned it is governed. Runtime counters
live here; what each task is, which files it changed and how it ended goes to the ledger.
"""

from __future__ import annotations

import collections
import contextvars
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import config, ledger, util

logger = logging.getLogger("hermes.plugins.proactive_agent")

_lock = threading.RLock()
_workers: Dict[str, dict] = {}
_by_session: Dict[str, str] = {}

_STALE_AFTER_S = 6 * 3600.0
_DONE_KEEP_S = 600.0
# The status values of handoff.DEFAULT_OUTPUT_SCHEMA, plus words workers use for the same outcomes.
REPORT_STATUSES = ("completed", "partial", "blocked", "failed")
_DONE_STATUSES = ("completed", "complete", "success", "done")
_FAILED_STATUSES = ("error", "failure", "aborted", "cancelled", "canceled")
_WRITE_HINTS = ("write", "edit", "patch")
_READ_HINTS = ("read", "search", "list", "view")


# ---------------------------------------------------------------- registry

def lookup(task_id: str = "", session_id: str = "") -> Optional[dict]:
    with _lock:
        if task_id and task_id in _workers:
            return _workers[task_id]
        wid = _by_session.get(session_id or "")
        return _workers.get(wid) if wid else None


def running_tasks(parent_session: str) -> List[dict]:
    return ledger.running(parent_session)


def mark_stopped_by_parent(subagent_id: str, parent_session: str) -> None:
    with _lock:
        w = _workers.get(subagent_id or "")
        if w is not None:
            w["stopped_by_parent"] = True
    ledger.mark_stopped(subagent_id, parent_session)


def _prune(now: float) -> None:
    for wid in [wid for wid, w in _workers.items()
                if (w["done"] and now - w["last_activity"] > _DONE_KEEP_S) or now - w["spawn"] > _STALE_AFTER_S]:
        _by_session.pop(_workers.pop(wid).get("session_id") or "", None)


def on_subagent_start(is_parent_governed: Callable[[str], bool], language_of: Callable[[str], str], **kw: Any) -> None:
    parent_session = kw.get("parent_session_id") or ""
    child_id = kw.get("child_subagent_id") or ""
    if not child_id:
        return
    with _lock:
        parent_worker = _workers.get(kw.get("parent_subagent_id") or "")
    if parent_worker is None and not is_parent_governed(parent_session):
        return
    owner = parent_worker["parent_session"] if parent_worker else parent_session
    now = time.monotonic()
    worker = {
        "id": child_id,
        "session_id": kw.get("child_session_id") or "",
        "parent_session": owner,
        "goal": str(kw.get("child_goal") or ""),
        "lang": parent_worker["lang"] if parent_worker else language_of(owner),
        "spawn": now,
        "last_activity": now,
        "last_call": "",
        "rounds": set(),
        "repeats": collections.Counter(),
        "pending_note": None,
        "stop_reason": None,
        "stop_noted": False,
        "done": False,
        "stopped_by_parent": False,
        # Captured on the parent's thread so the stuck alert reaches the user's chat later.
        "post_context": contextvars.copy_context(),
    }
    with _lock:
        _prune(now)
        _workers[child_id] = worker
        if worker["session_id"]:
            _by_session[worker["session_id"]] = child_id
    ledger.start(child_id, owner, worker["goal"], worker["session_id"])


def record_dispatch(result: Any) -> None:
    """Keep each task's live transcript path from the delegate_task dispatch response."""
    data = util.parse_json(result)
    if not isinstance(data, dict):
        return
    ids, paths = data.get("subagent_ids"), data.get("live_transcripts")
    if isinstance(ids, list) and isinstance(paths, list) and len(ids) == len(paths):
        for sid, path in zip(ids, paths):
            if isinstance(sid, str) and sid and isinstance(path, str) and path:
                ledger.set_transcript(sid, path)


# ---------------------------------------------------------------- runtime checks

def _stop_message(w: dict) -> str:
    return (f"[proactive-agent] STOP: {w['stop_reason']}. Do not call more tools. Write your final report now: "
            "status \"blocked\", what you tried, the exact blocker, and what you need from the main agent or the user.")


def _is_write(tool_name: str) -> bool:
    name = (tool_name or "").lower()
    return any(h in name for h in _WRITE_HINTS) and not any(h in name for h in _READ_HINTS)


def _succeeded(result: Any, status: Any) -> bool:
    if status not in (None, "success", "ok"):
        return False
    data = util.json_object(result)
    return not (data is not None and (data.get("error") or data.get("ok") is False or data.get("success") is False))


def pre_tool_call(w: dict, tool_name: str, args: Optional[dict], **kw: Any) -> Optional[dict]:
    with _lock:
        w["last_activity"] = time.monotonic()
        w["last_call"] = util.describe_call(tool_name, args)
        round_id = kw.get("api_request_id") or kw.get("tool_call_id") or ""
        if round_id:
            w["rounds"].add(round_id)
        if w["stop_reason"]:
            w["stop_noted"] = True
            return util.block(_stop_message(w))
    return None


def post_tool_call(w: dict, tool_name: str, args: Optional[dict], result: Any, **kw: Any) -> None:
    if kw.get("error_type") == "plugin_block":
        return
    args = args or {}
    if _is_write(tool_name) and isinstance(args.get("path"), str) and _succeeded(result, kw.get("status")):
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
            w["pending_note"] = (f"[proactive-agent] You ran the same step {count} times with the same result: {step}. "
                                 f"Change approach or report the blocker; at {limit} repeats you will be stopped.")
    if stopped:
        logger.warning("proactive-agent: stopping worker %s: %s", w["id"], stopped)
        if w["lang"] == "zh":
            _post(w, f"后台子任务卡住了，已叫停：{util.short(w['goal'], 40)}。主助手会接着处理。")
        else:
            _post(w, f"A background task got stuck and was stopped: {util.short(w['goal'], 40)}. "
                     "The assistant will follow up.")


def transform_tool_result(w: dict, tool_name: str, result: Any) -> Optional[str]:
    with _lock:
        if w["stop_reason"] and not w["stop_noted"]:
            w["stop_noted"] = True
            return util.append_note(result, _stop_message(w))
        note, w["pending_note"] = w["pending_note"], None
    return util.append_note(result, note) if note else None


# ---------------------------------------------------------------- results

def _written_paths(history: Any) -> List[str]:
    paths: List[str] = []
    for item in history if isinstance(history, list) else []:
        if not isinstance(item, dict) or not _is_write(str(item.get("tool_name") or "")) \
                or item.get("status") == "error":
            continue
        targets = (item.get("tool_input") or {}).get("targets") if isinstance(item.get("tool_input"), dict) else None
        value = targets.get("path") if isinstance(targets, dict) else None
        paths.extend(v for v in (value if isinstance(value, list) else [value]) if isinstance(v, str))
    return paths


def _strings(value: Any) -> List[str]:
    return [str(v) for v in value if str(v).strip()] if isinstance(value, list) else []


def _report(summary: Any) -> dict:
    """The worker's structured report: a summary that is itself a JSON object with a status or a summary.
    Plain text that merely quotes some JSON is not a report."""
    data = util.json_object(summary)
    return data if data is not None and ("status" in data or "summary" in data) else {}


def _outcome(report: dict, child_status: Any) -> str:
    """How the task ended. Hermes' failed/interrupted wins over a self-reported success, and a status word
    outside the schema still counts as not done rather than falling back to Hermes' "completed"."""
    hermes = str(child_status or "").lower()
    own = str(report.get("status") or "").strip().lower()
    if own in _DONE_STATUSES:
        own = "completed"
    elif own in _FAILED_STATUSES:
        own = "failed"
    elif own and own not in REPORT_STATUSES:
        own = "partial"
    if hermes in ("failed", "interrupted") and own in ("", "completed"):
        return hermes
    return own or (hermes if hermes in ("completed", "failed", "interrupted") else "unknown")


def on_subagent_stop(**kw: Any) -> None:
    child_session = kw.get("child_session_id") or ""
    with _lock:
        wid = _by_session.get(child_session)
        w = _workers.get(wid) if wid else None
        if w is not None:
            w["done"] = True
            w["last_activity"] = time.monotonic()

    report = _report(kw.get("child_summary"))
    status = _outcome(report, kw.get("child_status"))
    evidence, blockers = _strings(report.get("evidence")), _strings(report.get("blockers"))
    summary = str(report.get("summary") or kw.get("child_summary") or "")
    files = _written_paths(kw.get("tool_call_history"))

    if w is None:
        # The chat was reset or the plugin reloaded mid-run: close the record, no note to deliver.
        known = ledger.by_child(child_session)
        if known and known["status"] in (ledger.RUNNING, ledger.STOPPED, ledger.LOST):
            final = ledger.STOPPED if known["status"] == ledger.STOPPED else status
            ledger.finish(known["id"], final, summary, evidence, blockers, files)
        return

    if w["stop_reason"]:
        final = "blocked"
    elif w["stopped_by_parent"]:
        final = ledger.STOPPED
    else:
        final = status
    ledger.finish(w["id"], final, summary, evidence, blockers, files)

    minutes = (kw.get("duration_ms") or 0) / 60000.0
    lines = [f"[proactive-agent] Subagent finished: \"{util.short(w['goal'], 80)}\" "
             f"(status: {final}, {len(w['rounds'])} tool rounds, {minutes:.1f} min)."]
    if w["stop_reason"]:
        lines.append(f"- The plugin stopped it: {w['stop_reason']}. Treat it as blocked and decide the next step "
                     "yourself; do not re-dispatch the same plan.")
    elif final == ledger.STOPPED:
        record = ledger.get(w["id"]) or {}
        changed = record.get("files") or []
        lines.append("- You stopped it (action='stop'). Nothing to report unless the user asks about it."
                     + (f" It had changed: {', '.join(changed[:5])}." if changed else "")
                     + " A follow-up subagent is briefed with its partial work automatically.")
    elif final == "completed":
        if evidence:
            lines.append("- Evidence it reported: " + "; ".join(util.short(e, 120) for e in evidence[:5])
                         + ". Spot-check the real end state before telling the user.")
        else:
            lines.append("- It claims success without evidence. Verify the real end state before telling the user.")
    else:
        lines.append("- It did not finish. Read its blockers and tell the user plainly what is left. A follow-up "
                     "subagent is briefed with this task's record automatically.")
    queue_note(w["parent_session"], "\n".join(lines))


def lost_note(task: dict) -> str:
    files = task.get("files") or []
    changed = f" It had changed: {', '.join(files[:5])}." if files else ""
    return (f"[proactive-agent] Hermes restarted while a subagent was running: \"{util.short(task.get('goal'), 80)}\" "
            f"(started {ledger.minutes_since(task.get('started'))} min ago). Its result is lost and its side effects "
            f"are unknown.{changed} Check the real state before re-dispatching or telling the user it is done.")


def queue_note(parent_session: str, note: str) -> None:
    ledger.add_note(parent_session, note)


def pop_notes(parent_session: str) -> str:
    return "\n\n".join(ledger.pop_notes(parent_session))


# ---------------------------------------------------------------- chat alert

def _post(w: dict, text: str) -> None:
    ctx = config.ctx()
    poster = getattr(ctx, "post_message", None) if ctx is not None else None
    captured = w.get("post_context")
    if poster is None or captured is None:
        return

    def _send() -> None:
        try:
            handle = captured.copy().run(poster, text)
            if getattr(handle, "ok", True) is False:
                logger.debug("proactive-agent: chat alert refused: %s", getattr(handle, "error", ""))
        except Exception:
            logger.debug("proactive-agent: chat alert failed", exc_info=True)

    threading.Thread(target=_send, name="proactive-agent-post", daemon=True).start()


# ---------------------------------------------------------------- cleanup

def forget_parent(parent_session: str) -> None:
    """Drop in-memory state for one chat. The ledger keeps its records and pending notes."""
    if not parent_session:
        return
    with _lock:
        for wid in [wid for wid, w in _workers.items() if w["parent_session"] == parent_session]:
            _by_session.pop(_workers.pop(wid).get("session_id") or "", None)


def reset_all() -> None:
    with _lock:
        _workers.clear()
        _by_session.clear()

