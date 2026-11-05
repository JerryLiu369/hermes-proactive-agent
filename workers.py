"""Delegated workers: registry, runtime guards, result checks and progress updates.

A worker is governed only when the main agent that spawned it is governed. Its handoff
(recorded by the delegate_task gate) decides what it may do; the hooks here enforce it.
"""

from __future__ import annotations

import collections
import contextvars
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from . import config, guards

logger = logging.getLogger("hermes.plugins.proactive_agent")

_lock = threading.RLock()
_pending_handoffs: Dict[str, List[dict]] = collections.defaultdict(list)
_workers: Dict[str, dict] = {}
_by_session: Dict[str, str] = {}
_notes: Dict[str, List[str]] = collections.defaultdict(list)
_timers: Dict[str, threading.Timer] = {}

_WRITE_TOOLS = {"write_file", "patch"}
_STALE_AFTER_S = 6 * 3600.0
_PENDING_TTL_S = 900.0


# ---------------------------------------------------------------- registry

def expect_children(parent_session: str, handoffs: List[dict]) -> None:
    """Record handoffs the gate just approved; subagent_start claims them by goal."""
    now = time.monotonic()
    with _lock:
        pending = [h for h in _pending_handoffs.get(parent_session, []) if now - h["at"] < _PENDING_TTL_S]
        for h in handoffs:
            pending.append({**h, "at": now})
        _pending_handoffs[parent_session] = pending


def _claim_handoff(parent_session: str, goal: str) -> Optional[dict]:
    with _lock:
        pending = _pending_handoffs.get(parent_session) or []
        for matcher in (lambda h: h["goal"] == goal,
                        lambda h: h["goal"].strip()[:60] == (goal or "").strip()[:60]):
            for i, h in enumerate(pending):
                if matcher(h):
                    return pending.pop(i)
        return None


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
                if (w["done"] and now - w["last_activity"] > 600) or now - w["spawn_mono"] > _STALE_AFTER_S]:
        w = _workers.pop(wid)
        _by_session.pop(w.get("session_id") or "", None)


def on_subagent_start(is_parent_governed, **kw: Any) -> None:
    parent_session = kw.get("parent_session_id") or ""
    parent_sub = kw.get("parent_subagent_id") or ""
    child_id = kw.get("child_subagent_id") or ""
    if not child_id:
        return
    with _lock:
        parent_worker = _workers.get(parent_sub) if parent_sub else None
    if parent_worker is None and not is_parent_governed(parent_session):
        return
    goal = str(kw.get("child_goal") or "")
    owner_session = parent_worker["parent_session"] if parent_worker else parent_session
    handoff = _claim_handoff(owner_session, goal) or {}
    if parent_worker is not None and not handoff:
        handoff = {"risky_ok": parent_worker["risky_ok"], "targets": set(), "lang": parent_worker["lang"]}
    now_mono = time.monotonic()
    worker = {
        "id": child_id,
        "session_id": kw.get("child_session_id") or "",
        "parent_session": owner_session,
        "goal": goal,
        "risky_ok": bool(handoff.get("risky_ok")),
        "targets": set(handoff.get("targets") or ()),
        "lang": handoff.get("lang") or "en",
        "spawn_wall": time.time(),
        "spawn_mono": now_mono,
        "last_activity": now_mono,
        "rounds": set(),
        "outcomes": collections.Counter(),
        "failures": collections.Counter(),
        "observed": {},
        "last_call": "",
        "stop_reason": None,
        "stop_noted": False,
        "budget_warned": False,
        "done": False,
        "stopped_by_parent": False,
        # Captured on the parent's thread so progress posts reach the user's chat later.
        "post_context": contextvars.copy_context(),
    }
    with _lock:
        _prune(now_mono)
        _workers[child_id] = worker
        if worker["session_id"]:
            _by_session[worker["session_id"]] = child_id
    _ensure_progress_timer(owner_session)


# ---------------------------------------------------------------- runtime guards

def _stop_message(w: dict) -> str:
    return (
        f"[proactive-agent] STOP: {w['stop_reason']}. Do not call more tools. Write your final report now: "
        "status \"blocked\", what you tried, the exact blocker (quote the error), and what you need from the "
        "main agent or the user. A clear blocker report is a good result."
    )


def _abs_path(path: Any) -> Optional[str]:
    if not isinstance(path, str) or not path.strip():
        return None
    p = os.path.expanduser(path.strip())
    return os.path.normpath(p) if os.path.isabs(p) else None


def _mtime(path: str) -> Optional[float]:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def pre_tool_call(w: dict, tool_name: str, args: Optional[dict], **kw: Any) -> Optional[dict]:
    with _lock:
        w["last_activity"] = time.monotonic()
        w["last_call"] = guards.describe_call(tool_name, args)
        api_request_id = kw.get("api_request_id") or kw.get("tool_call_id") or ""
        if api_request_id:
            w["rounds"].add(api_request_id)
        rounds = len(w["rounds"])
        if w["stop_reason"] is None and rounds > int(config.get("worker_max_tool_rounds")):
            w["stop_reason"] = f"step budget reached ({config.get('worker_max_tool_rounds')} tool rounds)"
        if w["stop_reason"]:
            w["stop_noted"] = True
            return _block(_stop_message(w))

    command = guards.command_of(tool_name, args)
    scan = guards.scan_reason(command)
    if scan:
        return _block(
            f"[proactive-agent] Blocked: {scan} can run for minutes and flood your context. Target the exact "
            "file or directory you need (for config, read the known file path directly), or report what you need."
        )
    if config.get("worker_guard_risky_commands") and not w["risky_ok"]:
        risky = guards.risky_reason(command)
        if risky:
            return _block(
                f"[proactive-agent] Blocked: {risky} was not authorized by your handoff with a rollback path. "
                "Do not try to work around this. Report exactly what change you need and how it could be rolled back."
            )

    if config.get("worker_write_guard") and tool_name in _WRITE_TOOLS:
        path = _abs_path((args or {}).get("path"))
        if path:
            current = _mtime(path)
            with _lock:
                seen = w["observed"].get(path)
            if current is not None and current > w["spawn_wall"] and seen != current:
                return _block(
                    f"[proactive-agent] Blocked write: {path} was changed by someone else after you started "
                    "(another subagent or the main agent). Re-read it first. If your change conflicts with what is "
                    "there now, do not overwrite it; report the conflict instead."
                )
    return None


def _block(message: str) -> dict:
    return {"action": "block", "message": message, "reason": message}


def post_tool_call(w: dict, tool_name: str, args: Optional[dict], result: Any, **kw: Any) -> None:
    if kw.get("error_type") == "plugin_block":
        return
    failed, outcome = guards.outcome_of(result)
    tripped = None
    limit = max(2, int(config.get("worker_repeat_limit")))
    with _lock:
        w["last_activity"] = time.monotonic()
        if tool_name in _WRITE_TOOLS or tool_name == "read_file":
            path = _abs_path((args or {}).get("path"))
            if path and not failed:
                w["observed"][path] = _mtime(path)
        same_call = (guards.call_signature(tool_name, args), outcome)
        w["outcomes"][same_call] += 1
        if w["outcomes"][same_call] >= limit:
            tripped = f"repeated the same step {limit} times with the same result: " \
                      f"{guards.describe_call(tool_name, args)} -> \"{guards.short(outcome, 120)}\""
        if failed:
            w["failures"][(tool_name, outcome)] += 1
            if w["failures"][(tool_name, outcome)] >= limit and tripped is None:
                tripped = f"hit the same failure {limit} times: \"{guards.short(outcome, 120)}\""
        if tripped and w["stop_reason"] is None:
            w["stop_reason"] = tripped
        else:
            tripped = None
    if tripped:
        logger.warning("proactive-agent: stopping worker %s: %s", w["id"], tripped)
        if w["lang"] == "zh":
            _post(w, f"后台子任务卡住了，已叫停：{guards.short(w['goal'], 40)}（{guards.short(tripped, 80)}）。主助手会接着处理。")
        else:
            _post(w, f"A background task got stuck and was stopped: {guards.short(w['goal'], 40)} "
                     f"({guards.short(tripped, 80)}). The assistant will follow up.")


def transform_tool_result(w: dict, tool_name: str, result: Any) -> Optional[str]:
    with _lock:
        if w["stop_reason"] and not w["stop_noted"]:
            w["stop_noted"] = True
            return guards.append_note(result, _stop_message(w))
        budget = int(config.get("worker_max_tool_rounds"))
        if not w["budget_warned"] and len(w["rounds"]) >= max(1, int(budget * 0.8)):
            w["budget_warned"] = True
            return guards.append_note(
                result,
                f"[proactive-agent] {len(w['rounds'])}/{budget} tool rounds used. Wrap up: verify the end state "
                "and write your report soon, or report a blocker.",
            )
    return None


# ---------------------------------------------------------------- results

def _evidence_from(summary: Any) -> tuple[Optional[dict], List[str]]:
    parsed = guards.parse_json(summary)
    if isinstance(parsed, dict):
        evidence = parsed.get("evidence")
        if isinstance(evidence, list):
            return parsed, [str(e) for e in evidence if str(e).strip()]
        return parsed, []
    return None, []


def on_subagent_stop(**kw: Any) -> None:
    child_session = kw.get("child_session_id") or ""
    with _lock:
        wid = _by_session.get(child_session)
        w = _workers.get(wid) if wid else None
        if w is None:
            return
        w["done"] = True
        w["last_activity"] = time.monotonic()
    if not config.get("verify_child_results"):
        return

    status = str(kw.get("child_status") or "unknown")
    summary = kw.get("child_summary")
    parsed, evidence = _evidence_from(summary)
    reported = str((parsed or {}).get("status") or status).lower()
    history = kw.get("tool_call_history") or []
    failures = [h for h in history if isinstance(h, dict) and str(h.get("status") or "").lower() in ("error", "failed")]
    minutes = (kw.get("duration_ms") or 0) / 60000.0

    lines = [f"[proactive-agent] Check this subagent result before telling the user: \"{guards.short(w['goal'], 80)}\" "
             f"(status: {reported}, {len(w['rounds'])} tool rounds, {minutes:.1f} min)."]
    if w["stop_reason"]:
        lines.append(f"- The plugin stopped it: {w['stop_reason']}. Treat it as blocked. Decide the next step yourself "
                     "(different approach, ask the user, or report the blocker); do not re-dispatch the same plan.")
    if reported in ("completed", "complete", "success", "done") and not w["stop_reason"]:
        if evidence:
            lines.append("- Evidence it reported: " + "; ".join(guards.short(e, 120) for e in evidence[:5])
                         + ". Spot-check the real end state (status code, service, file) yourself.")
        else:
            lines.append("- It claims success but gave no verifiable evidence (URL status, exit code, file path, "
                         "service state). Verify the real end state yourself before saying it is done.")
    if failures and reported in ("completed", "complete", "success", "done"):
        lines.append(f"- {len(failures)} of its tool calls failed along the way; make sure the final state is not one of them.")
    if reported in ("blocked", "failed", "partial", "error", "interrupted") and not w["stop_reason"]:
        lines.append("- It did not finish. Read its blockers and decide; tell the user plainly what is left.")
    with _lock:
        _notes[w["parent_session"]].append("\n".join(lines))


def pop_notes(parent_session: str) -> str:
    with _lock:
        notes = _notes.pop(parent_session, [])
    return "\n\n".join(notes)


def running_summary(parent_session: str) -> str:
    """One line per still-running worker, for the main agent."""
    active = active_for_parent(parent_session)
    if not active:
        return ""
    now = time.monotonic()
    rows = [f"- {w['id']}: \"{guards.short(w['goal'], 60)}\" running {(now - w['spawn_mono']) / 60:.0f} min, "
            f"{len(w['rounds'])} rounds, last: {guards.short(w['last_call'], 60) or 'starting'}" for w in active]
    return "[proactive-agent] Subagents still running:\n" + "\n".join(rows)


# ---------------------------------------------------------------- progress updates

def _post(w: dict, text: str) -> None:
    ctx = config.ctx()
    poster = getattr(ctx, "post_message", None) if ctx is not None else None
    captured = w.get("post_context")
    if not config.get("progress_push_enabled") or poster is None or captured is None:
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
    interval = float(config.get("progress_interval_seconds"))
    if not config.get("progress_push_enabled") or interval <= 0:
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
        mins = (now - w["spawn_mono"]) / 60
        last = guards.short(w["last_call"], 50) or ("准备中" if zh else "starting")
        if zh:
            rows.append(f"{i}. {guards.short(w['goal'], 40)}：已运行 {mins:.0f} 分钟，第 {len(w['rounds'])} 步，最近在做 {last}")
        else:
            rows.append(f"{i}. {guards.short(w['goal'], 40)}: {mins:.0f} min, step {len(w['rounds'])}, last: {last}")
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
            _pending_handoffs.clear()
            _notes.clear()
            _workers.clear()
            _by_session.clear()
            return
        timer = _timers.pop(parent_session, None)
        if timer is not None:
            timer.cancel()
        _pending_handoffs.pop(parent_session, None)
        _notes.pop(parent_session, None)
        for wid in [wid for wid, w in _workers.items() if w["parent_session"] == parent_session]:
            _by_session.pop(_workers.pop(wid).get("session_id") or "", None)
