"""Main chat agent ("secretary"): turn time budget and background detach for long commands."""

from __future__ import annotations

import itertools
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import config, context, handoff, util

logger = logging.getLogger("hermes.plugins.proactive_agent")

# Still available once the turn budget is spent: reading, delegating and wrapping up (memory, skills,
# the todo list, a question to the user). Names are Hermes' registered tool names.
RED_ALLOWED_TOOLS = {
    "delegate_task", "process_manage", "read_file", "search_files", "session_search", "memory",
    "skill_view", "skills_list", "skill_manage", "todo_list", "clarify",
}
# process_manage stays only for looking at background jobs or ending them, not for driving them.
RED_PROCESS_ACTIONS = {"list", "poll", "log", "kill"}
_KEEP_USER_MESSAGES = 6
# How Hermes marks a user message that arrived mid-turn: a steer row, or (busy_input_mode
# "interrupt" while the model is answering) a redirect row whose api_content carries this scaffold.
_STEER_MARKER = "[OUT-OF-BAND USER MESSAGE"
_REDIRECT_MARKER = "[This response was interrupted by a user correction.]"
# User-role rows that machinery wrote, not the user: Hermes' async-delegation wakes and this plugin's own.
_MACHINE_PREFIXES = ("[ASYNC DELEGATION", "[proactive-agent]")

_lock = threading.Lock()
_turns: Dict[str, dict] = {}
_user_words: Dict[str, List[str]] = {}
_local_turns = itertools.count(1)


def _new_turn(turn_id: str, seen: int = 0, session_key: str = "") -> dict:
    return {"turn_id": turn_id, "key": turn_id or f"local-{next(_local_turns)}", "start": time.monotonic(),
            "seen": seen, "interjections": 0, "noted": "green", "session_key": session_key}


def _turn(session_id: str, turn_id: str = "") -> dict:
    # Only on_turn_start begins a turn: a background review fork shares the session id with
    # another turn id, and must not reset the live turn.
    st = _turns.get(session_id)
    if st is None:
        st = _new_turn(turn_id)
        _turns[session_id] = st
    return st


def _interjections(messages: Any) -> List[str]:
    """The user messages Hermes delivered into a running turn, in order."""
    found = []
    for m in messages if isinstance(messages, list) else []:
        if not isinstance(m, dict):
            continue
        text = util.message_text(m)
        sidecar = m.get("api_content") if isinstance(m.get("api_content"), str) else ""
        if m.get("display_kind") == "steer" or _STEER_MARKER in text or _STEER_MARKER in sidecar:
            found.append(text)
        elif m.get("role") == "user" and (_REDIRECT_MARKER in text or _REDIRECT_MARKER in sidecar):
            found.append(text)
    return found


def _by_user(text: Any) -> bool:
    return isinstance(text, str) and bool(text.strip()) and not text.lstrip().startswith(_MACHINE_PREFIXES)


def _user_messages(messages: Any) -> List[str]:
    if not isinstance(messages, list):
        return []
    texts = (util.message_text(m) for m in messages if isinstance(m, dict) and m.get("role") == "user"
             and m.get("display_kind") != "internal_notification")
    return [t for t in texts if _by_user(t)]


def user_words(session_id: str) -> List[str]:
    with _lock:
        return list(_user_words.get(session_id) or [])


def zone(session_id: str) -> str:
    with _lock:
        st = _turns.get(session_id)
        if st is None:
            return "green"
        elapsed = time.monotonic() - st["start"]
    if elapsed >= float(config.get("turn_hard_seconds")):
        return "red"
    if elapsed >= float(config.get("turn_soft_seconds")):
        return "yellow"
    return "green"


# ---------------------------------------------------------------- hooks (main agent only)

def turn_facts(session_id: str, turn_id: str = "") -> Optional[dict]:
    """The running turn: ``key`` (Hermes' turn id, or a local one), ``interjections`` (user messages
    delivered into it so far) and the gateway ``session_key``. None for another turn's call."""
    with _lock:
        st = _turns.get(session_id)
        if st is None or (turn_id and st["turn_id"] and st["turn_id"] != turn_id):
            return None
        return {"key": st["key"], "interjections": st["interjections"], "session_key": st["session_key"]}


def on_turn_start(session_id: str, turn_id: str, platform: str, messages: Any, user_message: Any) -> None:
    context.remember_platform(session_id, platform)
    words = _user_messages(messages)
    if _by_user(user_message) and (not words or words[-1] != user_message):
        words.append(user_message)
    with _lock:
        _turns[session_id] = _new_turn(turn_id, len(_interjections(messages)),
                                       context.session_env("HERMES_SESSION_KEY"))
        _user_words[session_id] = words[-_KEEP_USER_MESSAGES:]


def on_api_request(session_id: str, turn_id: str, messages: Any) -> None:
    """A new user message inside the running turn restarts its clock and joins the user's words."""
    found = _interjections(messages)
    with _lock:
        st = _turns.get(session_id)
        if st is None or (turn_id and st["turn_id"] and st["turn_id"] != turn_id):
            return
        st["seen"] = min(st["seen"], len(found))  # compression may have dropped earlier rows
        new = found[st["seen"]:]
        if not new:
            return
        logger.info("proactive-agent: user wrote mid-turn in %s; turn budget reset", session_id)
        _user_words[session_id] = (_user_words.get(session_id, []) + new)[-_KEEP_USER_MESSAGES:]
        st.update(seen=len(found), interjections=st["interjections"] + len(new), start=time.monotonic(),
                  noted="green")


def pre_tool_call(session_id: str, turn_id: str, tool_name: str, args: Optional[dict]) -> Optional[dict]:
    args = args or {}
    if tool_name == "process_manage" and args.get("action") == "wait":
        return util.block("[proactive-agent] process_manage(action='wait') blocks the chat. Background jobs notify you "
                      "when they finish; tell the user what is running and end your turn.")
    with _lock:
        _turn(session_id, turn_id)
    paused = tool_name not in RED_ALLOWED_TOOLS or (
        tool_name == "process_manage" and str(args.get("action") or "") not in RED_PROCESS_ACTIONS)
    if paused and zone(session_id) == "red":
        hard = int(float(config.get("turn_hard_seconds")))
        return util.block(f"[proactive-agent] This turn has run for more than {hard}s, so editing and execution tools "
                          "are paused. Answer the user now with what you know, or hand the rest to delegate_task.")
    if tool_name == "delegate_task":
        return handoff.gate(session_id, turn_id, args, user_words(session_id))
    return None


def transform_tool_result(session_id: str, tool_name: str, result: Any) -> Optional[str]:
    if tool_name == "terminal":
        data = util.json_object(result)
        if data is not None and data.get("status") == "yielded_to_background":
            data = dict(data)
            detach = int(config.get("terminal_auto_detach_seconds"))
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
        if st is None or st["noted"] == current:
            return None
        st["noted"] = current
        elapsed = int(time.monotonic() - st["start"])
    if current == "red":
        note = (f"[proactive-agent] This turn has run for {elapsed}s. Editing and execution tools are now paused. "
                "Answer the user, or delegate the rest.")
    else:
        hard = int(float(config.get("turn_hard_seconds")))
        note = (f"[proactive-agent] This turn has run for {elapsed}s. Within the next 1-2 steps, answer the user "
                f"or hand the remaining work to delegate_task. Editing and execution tools pause at {hard}s.")
    return util.append_note(result, note)


def forget(session_id: str) -> None:
    with _lock:
        _turns.pop(session_id, None)
        _user_words.pop(session_id, None)


def reset_all() -> None:
    with _lock:
        _turns.clear()
        _user_words.clear()


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
