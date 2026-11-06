"""Delegated workers: step budget, fail-fast on repeated steps, result notes and progress lines.

A worker is governed only when the main agent that spawned it is governed.
"""

from __future__ import annotations

import collections
import contextvars
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import config, util

logger = logging.getLogger("hermes.plugins.proactive_agent")

_lock = threading.RLock()
_workers: Dict[str, dict] = {}
_by_session: Dict[str, str] = {}
_notes: Dict[str, List[str]] = collections.defaultdict(list)
_timers: Dict[str, threading.Timer] = {}

_STALE_AFTER_S = 6 * 3600.0
_DONE_KEEP_S = 600.0


# ---------------------------------------------------------------- registry

def lookup(task_id: str = "", session_id: str = "") -> Optional[dict]:
    with _lock:
        if task_id and task_id in _workers:
            return _workers[task_id]
        wid = _by_session.get(session_id or "")
        return _workers.get(wid) if wid else None


def active_for_parent(parent_session: str) -> List[dict]:
    with _lock:
        return [w for w in _workers.values()
                if w["parent_session"] == parent_session and not w["done"] and not w["stopped_by_parent"]]


def mark_stopped_by_parent(subagent_id: str) -> None:
    with _lock:
        w = _workers.get(subagent_id or "")
        if w is not None:
            w["stopped_by_parent"] = True


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
        "budget_warned": False,
        "done": False,
        "stopped_by_parent": False,
        # Captured on the parent's thread so progress lines reach the user's chat later.
        "post_context": contextvars.copy_context(),
    }
    with _lock:
        _prune(now)
        _workers[child_id] = worker
        if worker["session_id"]:
            _by_session[worker["session_id"]] = child_id
    _ensure_progress_timer(owner)


# ---------------------------------------------------------------- runtime checks

def _block(message: str) -> dict:
    return {"action": "block", "message": message, "reason": message}


def _stop_message(w: dict) -> str:
    return (f"[proactive-agent] STOP: {w['stop_reason']}. Do not call more tools. Write your final report now: "
            "status \"blocked\", what you tried, the exact blocker, and what you need from the main agent or the user.")


def pre_tool_call(w: dict, tool_name: str, args: Optional[dict], **kw: Any) -> Optional[dict]:
    with _lock:
        w["last_activity"] = time.monotonic()
        w["last_call"] = util.describe_call(tool_name, args)
        round_id = kw.get("api_request_id") or kw.get("tool_call_id") or ""
        if round_id:
            w["rounds"].add(round_id)
        budget = int(config.get("worker_max_tool_rounds"))
        if w["stop_reason"] is None and budget > 0 and len(w["rounds"]) > budget:
            w["stop_reason"] = f"step budget reached ({budget} tool rounds)"
        if w["stop_reason"]:
            w["stop_noted"] = True
            return _block(_stop_message(w))
    return None


def post_tool_call(w: dict, tool_name: str, args: Optional[dict], result: Any, **kw: Any) -> None:
    if kw.get("error_type") == "plugin_block":
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
        budget = int(config.get("worker_max_tool_rounds"))
        if not note and budget > 0 and not w["budget_warned"] and len(w["rounds"]) >= max(1, int(budget * 0.8)):
            w["budget_warned"] = True
            note = (f"[proactive-agent] {len(w['rounds'])}/{budget} tool rounds used. Wrap up: verify the end state "
                    "and write your report soon, or report a blocker.")
    return util.append_note(result, note) if note else None


# ---------------------------------------------------------------- results

def on_subagent_stop(**kw: Any) -> None:
    with _lock:
        wid = _by_session.get(kw.get("child_session_id") or "")
        w = _workers.get(wid) if wid else None
        if w is None:
            return
        w["done"] = True
        w["last_activity"] = time.monotonic()

    parsed = util.parse_json(kw.get("child_summary"))
    parsed = parsed if isinstance(parsed, dict) else {}
    status = str(parsed.get("status") or kw.get("child_status") or "unknown").lower()
    evidence = [str(e) for e in parsed.get("evidence") or [] if str(e).strip()] \
        if isinstance(parsed.get("evidence"), list) else []
    minutes = (kw.get("duration_ms") or 0) / 60000.0

    lines = [f"[proactive-agent] Subagent finished: \"{util.short(w['goal'], 80)}\" "
             f"(status: {status}, {len(w['rounds'])} tool rounds, {minutes:.1f} min)."]
    if w["stop_reason"]:
        lines.append(f"- The plugin stopped it: {w['stop_reason']}. Treat it as blocked and decide the next step "
                     "yourself; do not re-dispatch the same plan.")
    elif status in ("completed", "complete", "success", "done"):
        if evidence:
            lines.append("- Evidence it reported: " + "; ".join(util.short(e, 120) for e in evidence[:5])
                         + ". Spot-check the real end state before telling the user.")
        else:
            lines.append("- It claims success without evidence. Verify the real end state before telling the user.")
    else:
        lines.append("- It did not finish. Read its blockers and tell the user plainly what is left.")
    with _lock:
        _notes[w["parent_session"]].append("\n".join(lines))


def pop_notes(parent_session: str) -> str:
    with _lock:
        return "\n\n".join(_notes.pop(parent_session, []))


def running_summary(parent_session: str) -> str:
    """One line per still-running worker, for the main agent."""
    active = active_for_parent(parent_session)
    if not active:
        return ""
    now = time.monotonic()
    rows = [f"- {w['id']}: \"{util.short(w['goal'], 60)}\" running {(now - w['spawn']) / 60:.0f} min, "
            f"{len(w['rounds'])} rounds, last: {util.short(w['last_call'], 60) or 'starting'}" for w in active]
    return "[proactive-agent] Subagents still running:\n" + "\n".join(rows)


# ---------------------------------------------------------------- progress lines

def _post(w: dict, text: str) -> None:
    ctx = config.ctx()
    poster = getattr(ctx, "post_message", None) if ctx is not None else None
    captured = w.get("post_context")
    if poster is None or captured is None or int(config.get("progress_interval_seconds")) <= 0:
        return

    def _send() -> None:
        try:
            handle = captured.copy().run(poster, text)
            if getattr(handle, "ok", True) is False:
                logger.debug("proactive-agent: progress post refused: %s", getattr(handle, "error", ""))
        except Exception:
            logger.debug("proactive-agent: progress post failed", exc_info=True)

    threading.Thread(target=_send, name="proactive-agent-post", daemon=True).start()


def _ensure_progress_timer(parent_session: str) -> None:
    interval = int(config.get("progress_interval_seconds"))
    if interval <= 0:
        return
    with _lock:
        if parent_session in _timers:
            return
        timer = threading.Timer(interval, _progress_tick, args=(parent_session,))
        timer.daemon = True
        _timers[parent_session] = timer
    timer.start()


def _progress_tick(parent_session: str) -> None:
    with _lock:
        _timers.pop(parent_session, None)
    active = active_for_parent(parent_session)
    if not active:
        return
    now = time.monotonic()
    zh = active[0]["lang"] == "zh"
    rows = []
    for i, w in enumerate(active, 1):
        mins = (now - w["spawn"]) / 60
        last = util.short(w["last_call"], 50) or ("准备中" if zh else "starting")
        if zh:
            rows.append(f"{i}. {util.short(w['goal'], 40)}：已运行 {mins:.0f} 分钟，第 {len(w['rounds'])} 步，最近在做 {last}")
        else:
            rows.append(f"{i}. {util.short(w['goal'], 40)}: {mins:.0f} min, step {len(w['rounds'])}, last: {last}")
    header = f"后台进展：{len(active)} 个子任务在跑" if zh else f"Background progress: {len(active)} task(s) running"
    _post(active[0], header + "\n" + "\n".join(rows))
    _ensure_progress_timer(parent_session)


# ---------------------------------------------------------------- cleanup

def forget_parent(parent_session: str = "") -> None:
    with _lock:
        if not parent_session:
            for t in _timers.values():
                t.cancel()
            _timers.clear()
            _notes.clear()
            _workers.clear()
            _by_session.clear()
            return
        timer = _timers.pop(parent_session, None)
        if timer is not None:
            timer.cancel()
        _notes.pop(parent_session, None)
        for wid in [wid for wid, w in _workers.items() if w["parent_session"] == parent_session]:
            _by_session.pop(_workers.pop(wid).get("session_id") or "", None)
