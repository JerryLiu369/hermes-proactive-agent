"""Main chat agent ("secretary") governance: turn budget, background detach, scans, and the
conversation snapshot the handoff gate summarizes."""

from __future__ import annotations

import collections
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import config, context, guards, handoff, workers

logger = logging.getLogger("hermes.plugins.proactive_agent")

# Tools that stay available once the turn budget is spent: reading, delegating and wrapping up.
RED_ALLOWED_TOOLS = {
    "delegate_task", "process_manage", "read_file", "search_files", "session_search", "memory",
    "skill_view", "skills_list", "skill_manage", "todo", "clarify", "send_message",
}

_lock = threading.Lock()
_turns: Dict[str, dict] = {}
_history: Dict[str, List[dict]] = {}


def _new_turn(turn_id: str, steers: int = 0) -> dict:
    return {
        "turn_id": turn_id,
        "start": time.monotonic(),
        "output_chars": 0,
        "steers": steers,
        "yellow_noted": False,
        "red_noted": False,
        "trace": collections.deque(maxlen=16),
    }


def _turn(session_id: str, turn_id: str = "") -> dict:
    st = _turns.get(session_id)
    if st is None or (turn_id and st["turn_id"] and st["turn_id"] != turn_id):
        st = _new_turn(turn_id)
        _turns[session_id] = st
    return st


def count_steers(messages: Any) -> int:
    if not isinstance(messages, list):
        return 0
    count = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("display_kind") == "steer" or "[OUT-OF-BAND USER MESSAGE" in guards.message_text(m):
            count += 1
    return count


def _snapshot(messages: Any) -> List[dict]:
    if not isinstance(messages, list):
        return []
    out = []
    for m in messages[-40:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            text = guards.message_text(m)
            if text.strip():
                out.append({"role": m["role"], "text": text[:4000]})
    return out


def language(session_id: str) -> str:
    with _lock:
        history = list(_history.get(session_id) or [])
    for m in reversed(history):
        if m["role"] == "user":
            return "zh" if guards.is_cjk(m["text"]) else "en"
    return "en"


def zone(session_id: str) -> str:
    with _lock:
        st = _turns.get(session_id)
        if st is None:
            return "green"
        elapsed, chars = time.monotonic() - st["start"], st["output_chars"]
    if elapsed >= float(config.get("turn_hard_seconds")) or chars >= int(config.get("turn_hard_output_chars")):
        return "red"
    if elapsed >= float(config.get("turn_soft_seconds")) or chars >= int(config.get("turn_soft_output_chars")):
        return "yellow"
    return "green"


# ---------------------------------------------------------------- hooks (main agent only)

def on_turn_start(session_id: str, turn_id: str, platform: str, messages: Any, user_message: Any) -> Optional[dict]:
    context.remember_platform(session_id, platform)
    snapshot = _snapshot(messages)
    if isinstance(user_message, str) and user_message.strip() and not (
            snapshot and snapshot[-1]["role"] == "user" and snapshot[-1]["text"] == user_message[:4000]):
        snapshot.append({"role": "user", "text": user_message[:4000]})
    with _lock:
        _turns[session_id] = _new_turn(turn_id, count_steers(messages))
        _history[session_id] = snapshot
    parts = [p for p in (workers.pop_notes(session_id), workers.running_summary(session_id)) if p]
    return {"context": "\n\n".join(parts)} if parts else None


def on_api_request(session_id: str, turn_id: str, messages: Any) -> None:
    steers = count_steers(messages)
    with _lock:
        st = _turn(session_id, turn_id)
        if steers > st["steers"]:
            logger.info("proactive-agent: user steered mid-turn in %s; turn budget reset", session_id)
            fresh = _new_turn(turn_id or st["turn_id"], steers)
            fresh["trace"] = st["trace"]
            _turns[session_id] = fresh
        if isinstance(messages, list) and messages:
            _history[session_id] = _snapshot(messages) or _history.get(session_id, [])


def _block(message: str) -> dict:
    return {"action": "block", "message": message, "reason": message}


def pre_tool_call(session_id: str, turn_id: str, tool_name: str, args: Optional[dict]) -> Optional[dict]:
    args = args or {}
    if tool_name == "process_manage" and args.get("action") == "wait":
        return _block("[proactive-agent] process_manage(action='wait') is not allowed in the chat agent: it blocks "
                      "the conversation. Background jobs notify you when they finish; tell the user and end your turn.")
    scan = guards.scan_reason(guards.command_of(tool_name, args))
    if scan:
        return _block(f"[proactive-agent] Blocked: {scan} can hang for minutes. Read the exact path you need instead.")
    with _lock:
        _turn(session_id, turn_id)
    current = zone(session_id)
    if current == "red" and tool_name not in RED_ALLOWED_TOOLS:
        hard = int(float(config.get("turn_hard_seconds")))
        return _block(
            f"[proactive-agent] Turn budget spent (>{hard}s or too much tool output). Editing and execution tools are "
            "frozen for this turn. Either answer the user now with what you know, or hand the rest to delegate_task "
            "with a complete handoff. Do not retry this tool."
        )
    if tool_name == "delegate_task":
        with _lock:
            history = list(_history.get(session_id) or [])
            st = _turns.get(session_id)
            trace = list(st["trace"]) if st else []
        return handoff.gate(session_id, turn_id, args, history, trace, language(session_id))
    return None


def post_tool_call(session_id: str, turn_id: str, tool_name: str, args: Optional[dict], result: Any) -> None:
    text = result if isinstance(result, str) else str(result or "")
    with _lock:
        st = _turn(session_id, turn_id)
        st["output_chars"] += len(text)
        if tool_name != "delegate_task":
            st["trace"].append({"call": guards.describe_call(tool_name, args), "result": text[:600]})


def transform_tool_result(session_id: str, tool_name: str, result: Any) -> Optional[str]:
    if tool_name == "terminal":
        data = guards.parse_json(result)
        if isinstance(data, dict) and data.get("status") == "yielded_to_background":
            detach = config.get("terminal_auto_detach_seconds")
            data["note"] = (
                f"[proactive-agent] Still running: this command took longer than {detach}s, so it moved to the "
                "background. It has NOT failed. `output` holds what it printed so far. You will be notified "
                "automatically when it finishes; do not poll, sleep or re-run it. Tell the user briefly what is "
                "running and end your turn, or continue with work that does not depend on it."
            )
            return json.dumps(data, ensure_ascii=False)
    current = zone(session_id)
    if current == "green":
        return None
    with _lock:
        st = _turns.get(session_id)
        if st is None:
            return None
        elapsed, chars = int(time.monotonic() - st["start"]), st["output_chars"]
        if current == "red" and not st["red_noted"]:
            st["red_noted"] = st["yellow_noted"] = True
            note = (f"[proactive-agent] Turn budget spent ({elapsed}s, {chars} chars of tool output). Editing and "
                    "execution tools are now frozen. Answer the user or delegate the rest with a complete handoff.")
        elif current == "yellow" and not st["yellow_noted"]:
            st["yellow_noted"] = True
            hard = int(float(config.get("turn_hard_seconds")))
            note = (f"[proactive-agent] This turn has used {elapsed}s and {chars} chars of tool output. Within the "
                    "next 1-2 steps, either answer the user or hand the remaining work to delegate_task with a "
                    f"complete handoff. Editing and execution tools freeze at {hard}s.")
        else:
            return None
    return guards.append_note(result, note)


def forget(session_id: str = "") -> None:
    with _lock:
        if session_id:
            _turns.pop(session_id, None)
            _history.pop(session_id, None)
        else:
            _turns.clear()
            _history.clear()


# ---------------------------------------------------------------- background detach

_patched = False
_patch_lock = threading.Lock()


def _governed_tool_thread() -> bool:
    """Called on the tool thread itself, where Hermes' session contextvars are bound."""
    if context.is_child() or context.is_cron():
        return False
    session_id = context.session_env("HERMES_SESSION_ID")
    platform = context.platform_for(session_id=session_id)
    return bool(platform) and platform in config.governed_platforms()


def install_detach_patch() -> None:
    """Wrap BaseEnvironment._wait_for_process so long foreground commands yield to the
    background through Hermes' own yield path (the process keeps running)."""
    global _patched
    with _patch_lock:
        if _patched:
            return
        try:
            from tools.environments.base import BaseEnvironment
            from tools.interrupt import request_yield
        except ImportError:
            logger.warning("proactive-agent: terminal backend not importable; background detach disabled")
            return
        original: Callable = BaseEnvironment._wait_for_process

        def _wait_for_process(self, proc, timeout: int = 120, *, bounded_capture: bool = False,
                              watch_interrupt_tid: Optional[int] = None, output=None,
                              yield_handler: Optional[Callable] = None) -> dict:
            call = dict(timeout=timeout, bounded_capture=bounded_capture, watch_interrupt_tid=watch_interrupt_tid,
                        output=output, yield_handler=yield_handler)
            detach = float(config.get("terminal_auto_detach_seconds"))
            if yield_handler is None or watch_interrupt_tid is None or detach <= 0 or not _governed_tool_thread():
                return original(self, proc, **call)
            state = {"done": False, "fired": False}
            state_lock = threading.Lock()

            def _fire() -> None:
                with state_lock:
                    if state["done"] or (getattr(proc, "poll", None) and proc.poll() is not None):
                        return
                    state["fired"] = True
                    try:
                        request_yield(watch_interrupt_tid)
                    except Exception as e:
                        logger.warning("proactive-agent: request_yield failed: %s", e)

            timer = threading.Timer(detach, _fire)
            timer.daemon = True
            timer.start()
            try:
                return original(self, proc, **call)
            finally:
                timer.cancel()
                with state_lock:
                    state["done"] = True
                    fired = state["fired"]
                if fired:
                    # Our yield may land after the process already exited; never leave it pending
                    # for the next command on this thread. A user's own yield is left alone.
                    try:
                        from tools.interrupt import consume_yield
                        consume_yield(watch_interrupt_tid)
                    except Exception:
                        pass

        BaseEnvironment._wait_for_process = _wait_for_process
        _patched = True
        logger.info("proactive-agent: background detach installed")
