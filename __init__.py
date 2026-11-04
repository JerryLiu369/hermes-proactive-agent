"""
proactive-agent: Proactive Agent Governor Plugin for Hermes Agent (v0.1.5).

Enforces proactive orchestrator discipline for human-interactive dialogue platforms (positive allowlist: weixin by default):
1. Positive Platform Allowlist (Zero Intrusion): Only platforms explicitly declared in `governed_platforms`
   (default: ["weixin"]) are subject to proactive governance. Unlisted platforms (e.g. cli, desktop) are
   completely exempt (0 intrusion).
2. Autonomous Subagent & Cron Exemption: Subagents (task_id.startswith('sa-') or is_delegated_child_context)
   and scheduled Cron tasks (_is_cron_context) completely bypass all restrictions.
3. Adaptive Terminal Background Detach (>30s): Detaches long-running foreground commands to background.
4. Turn Circuit Breaker (>8 rounds / >120s): Freezes heavy execution tools after quota exhaustion.
5. Dynamic System Prompt Discipline: Injects proactive orchestration rules into system prompt.
6. Human Intervention Self-Healing: Instant reset of turn quota when user steer message is detected.
"""

from __future__ import annotations

import collections.abc
import contextvars
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set

__version__ = "0.1.5"

logger = logging.getLogger("hermes.plugins.proactive_agent")

_ctx: Optional[Any] = None
_patched: bool = False
_state_lock = threading.Lock()

DEFAULT_MAX_ROUNDS = 8
DEFAULT_MAX_DURATION_S = 120.0

# Turn state: turn_id -> {
#   "session_id": str,
#   "turn_id": str,
#   "platform": str,
#   "start_time": float,
#   "seen_api_requests": set[str],
#   "round_count": int,
#   "cumulative_duration_s": float,
#   "steer_count": int,
#   "last_activity": float,
# }
_turn_states: Dict[str, Dict[str, Any]] = {}

_timeout_yield_tids: Set[int] = set()
_timeout_yield_lock = threading.Lock()

# Subagent context & thread mapping
_current_task_id: contextvars.ContextVar[str] = contextvars.ContextVar("proactive_agent_task_id", default="")
_thread_task_ids: Dict[int, str] = {}
_task_id_lock = threading.Lock()

# Cron context & thread mapping
_current_is_cron: contextvars.ContextVar[bool] = contextvars.ContextVar("proactive_agent_is_cron", default=False)
_thread_is_cron: Dict[int, bool] = {}

# Platform context & tracking
_current_platform: contextvars.ContextVar[str] = contextvars.ContextVar("proactive_agent_platform", default="")
_thread_platforms: Dict[int, str] = {}
_session_platforms: Dict[str, str] = {}
_turn_platforms: Dict[str, str] = {}


def _record_platform_context(
    platform: Optional[str],
    tid: Optional[int] = None,
    session_id: str = "",
    turn_id: str = "",
) -> None:
    """Record platform context on contextvar, thread mapping, session mapping, and turn mapping."""
    if not platform:
        return
    p = str(platform).strip()
    if not p:
        return
    _current_platform.set(p)
    target_tid = tid if tid is not None else threading.get_ident()
    with _state_lock:
        _thread_platforms[target_tid] = p
        if session_id:
            _session_platforms[session_id] = p
        if turn_id:
            _turn_platforms[turn_id] = p
            if turn_id in _turn_states:
                _turn_states[turn_id]["platform"] = p


def _get_current_platform(watch_interrupt_tid: Optional[int] = None, **hook_kwargs: Any) -> str:
    """Retrieve current platform from hook kwargs, session_info, contextvar, thread, or turn/session maps."""
    # 1. Direct hook_kwargs
    p = hook_kwargs.get("platform")
    if p:
        return str(p).strip()

    # 2. session_info (system prompt hook)
    session_info = hook_kwargs.get("session_info")
    if session_info is not None:
        p_info = ""
        if isinstance(session_info, (dict, collections.abc.Mapping)):
            p_info = str(session_info.get("platform") or "")
        elif hasattr(session_info, "get"):
            p_info = str(session_info.get("platform") or "")
        elif hasattr(session_info, "platform"):
            p_info = str(getattr(session_info, "platform", "") or "")
        if p_info:
            return p_info.strip()

    # 3. ContextVar
    cp = _current_platform.get()
    if cp:
        return cp.strip()

    # 4. Thread mapping
    target_tids = []
    if watch_interrupt_tid is not None:
        target_tids.append(watch_interrupt_tid)
    target_tids.append(threading.get_ident())

    with _state_lock:
        for tid in target_tids:
            t_plat = _thread_platforms.get(tid)
            if t_plat:
                return t_plat

        # 5. Turn / session mapping
        turn_id = hook_kwargs.get("turn_id") or ""
        if turn_id and turn_id in _turn_platforms:
            return _turn_platforms[turn_id]

        session_id = hook_kwargs.get("session_id") or ""
        if session_id and session_id in _session_platforms:
            return _session_platforms[session_id]

    return ""


def _record_cron_context(is_cron: bool = True, tid: Optional[int] = None) -> None:
    """Record cron context on current contextvar and thread mapping."""
    _current_is_cron.set(is_cron)
    with _task_id_lock:
        _thread_is_cron[tid if tid is not None else threading.get_ident()] = is_cron


def _is_cron_context(watch_interrupt_tid: Optional[int] = None, **hook_kwargs: Any) -> bool:
    """Check if the current execution context or thread belongs to a Cron task.

    Cron tasks are unattended autonomous background executions with no human waiting in
    interactive private chat. They must be completely exempt from interactive dialogue
    governance (discipline prompt injection, turn round circuit breakers, and 30s terminal detach).
    """
    # 1. Check ContextVar for task_id or cron flag
    task_id = _current_task_id.get()
    if task_id and (task_id.startswith("cron:") or task_id.startswith("cron_") or task_id == "cron"):
        _record_cron_context(True)
        return True

    if _current_is_cron.get():
        return True

    # 2. Direct hook_kwargs checks
    kw_task_id = hook_kwargs.get("task_id") or ""
    if kw_task_id and (kw_task_id.startswith("cron:") or kw_task_id.startswith("cron_") or kw_task_id == "cron"):
        _record_cron_context(True)
        return True

    session_id = hook_kwargs.get("session_id") or ""
    if session_id and (session_id.startswith("cron_") or session_id.startswith("cron:") or ":cron:" in session_id):
        _record_cron_context(True)
        return True

    turn_id = hook_kwargs.get("turn_id") or ""
    if turn_id and (":cron:" in turn_id or turn_id.startswith("cron_") or turn_id.startswith("cron:")):
        _record_cron_context(True)
        return True

    platform = hook_kwargs.get("platform") or ""
    if platform == "cron":
        _record_cron_context(True)
        return True

    session_info = hook_kwargs.get("session_info")
    if session_info is not None:
        p_info = ""
        sid_info = ""
        if isinstance(session_info, (dict, collections.abc.Mapping)):
            p_info = str(session_info.get("platform") or "")
            sid_info = str(session_info.get("session_id") or "")
        elif hasattr(session_info, "get"):
            p_info = str(session_info.get("platform") or "")
            sid_info = str(session_info.get("session_id") or "")
        elif hasattr(session_info, "platform"):
            p_info = str(getattr(session_info, "platform", "") or "")
            sid_info = str(getattr(session_info, "session_id", "") or "")
        if p_info == "cron" or sid_info.startswith("cron_") or sid_info.startswith("cron:") or ":cron:" in sid_info:
            _record_cron_context(True)
            return True

    # 3. Thread-local checks
    target_tids = []
    if watch_interrupt_tid is not None:
        target_tids.append(watch_interrupt_tid)
    target_tids.append(threading.get_ident())

    with _task_id_lock:
        for tid in target_tids:
            if _thread_is_cron.get(tid):
                return True
            t_id = _thread_task_ids.get(tid)
            if t_id and (t_id.startswith("cron:") or t_id.startswith("cron_") or t_id == "cron"):
                return True

    # 4. ContextVar / runtime checks from Hermes Core
    if _ctx is not None and hasattr(_ctx, "current_cron_execution"):
        try:
            if _ctx.current_cron_execution() is not None:
                _record_cron_context(True)
                return True
        except Exception:
            pass

    try:
        from cron.execution_identity import current_cron_execution
        if current_cron_execution() is not None:
            _record_cron_context(True)
            return True
    except Exception:
        pass

    try:
        from gateway.session_cron import current_execution
        if current_execution() is not None:
            _record_cron_context(True)
            return True
    except Exception:
        pass

    try:
        from gateway.session_context import get_session_env
        if get_session_env("HERMES_CRON_SESSION") == "1":
            _record_cron_context(True)
            return True
    except Exception:
        pass

    # 5. Environment variable checks
    if os.environ.get("HERMES_CRON_SESSION") == "1" or bool(os.environ.get("_HERMES_CRON_EXTERNAL_WORKER")):
        _record_cron_context(True)
        return True

    return False


def _is_subagent_context(watch_interrupt_tid: Optional[int] = None, **hook_kwargs: Any) -> bool:
    """Check if the current context or thread belongs to a subagent (task_id.startswith('sa-') or is_delegated_child_context)."""
    kw_task_id = hook_kwargs.get("task_id") or ""
    if kw_task_id and kw_task_id.startswith("sa-"):
        return True

    session_info = hook_kwargs.get("session_info")
    if session_info is not None:
        p_info = ""
        if isinstance(session_info, (dict, collections.abc.Mapping)):
            p_info = str(session_info.get("platform") or "")
        elif hasattr(session_info, "get"):
            p_info = str(session_info.get("platform") or "")
        elif hasattr(session_info, "platform"):
            p_info = str(getattr(session_info, "platform", "") or "")
        if p_info == "subagent":
            return True

    task_id = _current_task_id.get()
    if task_id and task_id.startswith("sa-"):
        return True

    target_tids = []
    if watch_interrupt_tid is not None:
        target_tids.append(watch_interrupt_tid)
    target_tids.append(threading.get_ident())

    with _task_id_lock:
        for tid in target_tids:
            t_id = _thread_task_ids.get(tid)
            if t_id and t_id.startswith("sa-"):
                return True

    try:
        from agent.delegation_context import is_delegated_child_context
        if is_delegated_child_context():
            return True
    except Exception:
        pass

    return False


def _get_setting(key: str, default: Any) -> Any:
    """Read a setting from env, then plugin config, then default."""
    env_key = f"PROACTIVE_{key.upper()}"
    if env_key in os.environ:
        try:
            val = os.environ[env_key]
            if isinstance(default, list):
                s = str(val).strip()
                if s.startswith("["):
                    parsed = json.loads(s)
                    if isinstance(parsed, list):
                        return [str(x).strip() for x in parsed]
                return [x.strip() for x in s.split(",") if x.strip()]
            return type(default)(val)
        except Exception:
            pass

    if _ctx is not None:
        try:
            val = _ctx.get_config(key, None)
            if val is not None:
                if isinstance(default, list):
                    if isinstance(val, list):
                        return [str(x).strip() for x in val]
                    elif isinstance(val, str):
                        s = val.strip()
                        if s.startswith("["):
                            parsed = json.loads(s)
                            if isinstance(parsed, list):
                                return [str(x).strip() for x in parsed]
                        return [x.strip() for x in s.split(",") if x.strip()]
                return type(default)(val)
        except Exception:
            pass

    return default


def _get_governed_platforms() -> List[str]:
    """Retrieve list of governed platforms from env, config, or default ['weixin']."""
    val = _get_setting("governed_platforms", ["weixin"])
    if isinstance(val, list):
        return [str(p).strip().lower() for p in val if str(p).strip()]
    return ["weixin"]


def _should_govern(watch_interrupt_tid: Optional[int] = None, **hook_kwargs: Any) -> bool:
    """Evaluate whether proactive agent governance applies to the current context.

    Governance applies (returns True) ONLY when ALL 3 checks pass:
    1. Positive Platform Allowlist: The current platform must be in `governed_platforms`
       (default: ["weixin"]). If not in list, empty, or unknown, returns False (0 intrusion).
    2. Subagent Bypass: If task_id starts with 'sa-' or is_delegated_child_context(), returns False.
    3. Cron Bypass: If execution context is a scheduled cron task, returns False.
    """
    # 0. Ingest & record context if provided
    platform_kw = hook_kwargs.get("platform")
    session_id = hook_kwargs.get("session_id") or ""
    turn_id = hook_kwargs.get("turn_id") or ""
    task_id = hook_kwargs.get("task_id") or ""
    session_info = hook_kwargs.get("session_info")

    if platform_kw:
        _record_platform_context(str(platform_kw).strip(), tid=watch_interrupt_tid, session_id=session_id, turn_id=turn_id)
    elif session_info is not None:
        p_info = ""
        s_info = ""
        if isinstance(session_info, (dict, collections.abc.Mapping)):
            p_info = str(session_info.get("platform") or "")
            s_info = str(session_info.get("session_id") or "")
        elif hasattr(session_info, "get"):
            p_info = str(session_info.get("platform") or "")
            s_info = str(session_info.get("session_id") or "")
        elif hasattr(session_info, "platform"):
            p_info = str(getattr(session_info, "platform", "") or "")
            s_info = str(getattr(session_info, "session_id", "") or "")
        if p_info:
            _record_platform_context(p_info.strip(), tid=watch_interrupt_tid, session_id=s_info or session_id, turn_id=turn_id)

    if task_id:
        _current_task_id.set(task_id)
        target_tid = watch_interrupt_tid if watch_interrupt_tid is not None else threading.get_ident()
        with _task_id_lock:
            _thread_task_ids[target_tid] = task_id

    # 判定 1：当前平台必须在 governed_platforms 中（默认 ["weixin"]）。如果不在或为空，直接返回 False（完全放行，0 侵入）！
    platform = _get_current_platform(watch_interrupt_tid=watch_interrupt_tid, **hook_kwargs)
    governed = _get_governed_platforms()
    if not platform or platform.lower() not in governed:
        return False

    # 判定 2：如果是 subagent（task_id 以 sa- 开头或 is_delegated_child_context），返回 False。
    if _is_subagent_context(watch_interrupt_tid=watch_interrupt_tid, **hook_kwargs):
        return False

    # 判定 3：如果是 cron（_is_cron_context），返回 False。
    if _is_cron_context(watch_interrupt_tid=watch_interrupt_tid, **hook_kwargs):
        return False

    # 只有三者都通过，才返回 True
    return True


_patch_lock = threading.Lock()


def _install_adaptive_terminal_patch() -> None:
    """Monkey-patch BaseEnvironment._wait_for_process to support adaptive background detach."""
    global _patched
    with _patch_lock:
        if _patched:
            return

        try:
            from tools.environments.base import BaseEnvironment
            from tools.interrupt import request_yield
        except ImportError:
            logger.warning("proactive-agent: Failed to import BaseEnvironment or request_yield")
            return

        orig_wait = BaseEnvironment._wait_for_process

        def _terminal_watch_wrapper(
            self,
            proc,
            timeout: int = 120,
            *,
            bounded_capture: bool = False,
            watch_interrupt_tid: Optional[int] = None,
            output=None,
            yield_handler: Optional[Callable] = None,
        ) -> dict:
            # Positive allowlist & bypass check: if not governed, bypass 30s auto-detach completely
            if not _should_govern(watch_interrupt_tid=watch_interrupt_tid):
                return orig_wait(
                    self,
                    proc,
                    timeout=timeout,
                    bounded_capture=bounded_capture,
                    watch_interrupt_tid=watch_interrupt_tid,
                    output=output,
                    yield_handler=yield_handler,
                )

            detach_timeout = _get_setting("terminal_auto_detach_seconds", 30)

            # Only apply timer if yield_handler is present, watch_interrupt_tid is known, and detach_timeout > 0
            if yield_handler is not None and watch_interrupt_tid is not None and detach_timeout > 0:
                stop_event = threading.Event()

                def _trigger_yield() -> None:
                    if stop_event.is_set():
                        return
                    if getattr(proc, "poll", None) and proc.poll() is not None:
                        return
                    try:
                        with _timeout_yield_lock:
                            _timeout_yield_tids.add(watch_interrupt_tid)
                        request_yield(watch_interrupt_tid)
                    except Exception as e:
                        logger.warning("proactive-agent: request_yield failed: %s", e)

                timer = threading.Timer(float(detach_timeout), _trigger_yield)
                timer.daemon = True
                timer.start()
                yielded = False
                try:
                    res = orig_wait(
                        self,
                        proc,
                        timeout=timeout,
                        bounded_capture=bounded_capture,
                        watch_interrupt_tid=watch_interrupt_tid,
                        output=output,
                        yield_handler=yield_handler,
                    )
                    if isinstance(res, dict) and (
                        res.get("status") == "yielded_to_background" or res.get("yielded_session_id")
                    ):
                        yielded = True
                    return res
                finally:
                    stop_event.set()
                    timer.cancel()
                    if not yielded:
                        with _timeout_yield_lock:
                            _timeout_yield_tids.discard(watch_interrupt_tid)
                    # Clean up unconsumed yield in tools.interrupt to prevent stale yield leaking to subsequent commands
                    try:
                        from tools.interrupt import consume_yield
                        consume_yield(watch_interrupt_tid)
                    except Exception:
                        pass
            else:
                return orig_wait(
                    self,
                    proc,
                    timeout=timeout,
                    bounded_capture=bounded_capture,
                    watch_interrupt_tid=watch_interrupt_tid,
                    output=output,
                    yield_handler=yield_handler,
                )

        _adaptive_wait_for_process = _terminal_watch_wrapper
        BaseEnvironment._wait_for_process = _terminal_watch_wrapper
        _patched = True
        logger.info("proactive-agent: Installed adaptive terminal background detach hook (default: 30s)")


def _count_steers(messages: Optional[list]) -> int:
    """Count mid-turn steer / out-of-band user messages in the message list."""
    if not isinstance(messages, list):
        return 0
    count = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("display_kind") == "steer":
            count += 1
        else:
            c = m.get("content")
            if isinstance(c, str) and "[OUT-OF-BAND USER MESSAGE" in c:
                count += 1
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and "[OUT-OF-BAND USER MESSAGE" in str(part.get("text", "")):
                        count += 1
                        break
    return count


def _reset_turn_state(turn_id: str, session_id: str = "", steer_count: int = 0) -> dict[str, Any]:
    """Atomically reset or initialize state for a turn, pruning older turns for the session."""
    now = time.monotonic()
    st = {
        "session_id": session_id,
        "turn_id": turn_id,
        "start_time": now,
        "seen_api_requests": set(),
        "round_count": 0,
        "cumulative_duration_s": 0.0,
        "steer_count": steer_count,
        "last_activity": now,
    }
    _turn_states[turn_id] = st
    # Prune stale turn entries for this session to prevent leak
    if session_id:
        for k in list(_turn_states.keys()):
            if k != turn_id and _turn_states[k].get("session_id") == session_id:
                _turn_states.pop(k, None)
    return st


def _on_pre_llm_call(**hook_kwargs: Any) -> None:
    """Reset turn state at the start of every new user turn (pre_llm_call)."""
    if not _should_govern(**hook_kwargs):
        return

    turn_id = hook_kwargs.get("turn_id") or ""
    session_id = hook_kwargs.get("session_id") or ""
    if turn_id:
        messages = hook_kwargs.get("conversation_history") or []
        initial_steer = _count_steers(messages)
        with _state_lock:
            logger.info(
                "proactive-agent: Initializing fresh turn state on pre_llm_call for turn %s (session %s)",
                turn_id, session_id
            )
            _reset_turn_state(turn_id, session_id=session_id, steer_count=initial_steer)


def _on_pre_api_request(**hook_kwargs: Any) -> None:
    """Detect mid-turn human intervention (steer messages) and increment round count."""
    if not _should_govern(**hook_kwargs):
        return

    turn_id = hook_kwargs.get("turn_id") or ""
    session_id = hook_kwargs.get("session_id") or ""
    if not turn_id:
        return

    messages = hook_kwargs.get("request_messages") or hook_kwargs.get("conversation_history") or []
    steer_count = _count_steers(messages)
    api_request_id = hook_kwargs.get("api_request_id") or ""

    with _state_lock:
        st = _turn_states.get(turn_id)
        if st is None:
            st = _reset_turn_state(turn_id, session_id=session_id, steer_count=steer_count)

        # Human intervention check: new steer message detected mid-turn!
        prev_steer = st.get("steer_count", 0)
        if steer_count > prev_steer:
            logger.info(
                "proactive-agent: Detected human intervention (steer count %d -> %d) in turn %s. Resetting turn rounds and timer.",
                prev_steer, steer_count, turn_id
            )
            now = time.monotonic()
            st["round_count"] = 0
            st["seen_api_requests"].clear()
            st["start_time"] = now
            st["steer_count"] = steer_count
            st["last_activity"] = now

        if api_request_id:
            if api_request_id not in st["seen_api_requests"]:
                st["seen_api_requests"].add(api_request_id)
                st["round_count"] = len(st["seen_api_requests"])
        else:
            st["round_count"] = st.get("round_count", 0) + 1


def _on_agent_loop_stopped(**hook_kwargs: Any) -> None:
    """Clear turn state when an agent turn is interrupted mid-run."""
    session_key = hook_kwargs.get("session_key") or hook_kwargs.get("session_id") or ""
    with _state_lock:
        if session_key:
            for k in list(_turn_states.keys()):
                if session_key in k or _turn_states[k].get("session_id") == session_key:
                    _turn_states.pop(k, None)
            _session_platforms.pop(session_key, None)
        else:
            _turn_states.clear()
            _session_platforms.clear()
        logger.info("proactive-agent: Cleared turn states on agent_loop_stopped for %s", session_key)


def _pre_tool_call(tool_name: str, args: Optional[dict] = None, **hook_kwargs: Any) -> Optional[dict]:
    """Pre-tool hook enforcing circuit breaker (>8 rounds or >120s)."""
    if not _should_govern(**hook_kwargs):
        return None

    if tool_name == "process_manage" and (args or {}).get("action") == "wait":
        reason = "[Proactive Agent Policy]: process_manage(action='wait') is forbidden on main thread to prevent blocking."
        logger.warning("proactive-agent: Blocked process_manage(wait) on main thread")
        return {
            "action": "block",
            "message": reason,
            "reason": reason,
        }

    turn_id = hook_kwargs.get("turn_id") or ""
    if not turn_id:
        return None

    session_id = hook_kwargs.get("session_id") or ""
    now = time.monotonic()

    with _state_lock:
        if len(_turn_states) > 50:
            for k in list(_turn_states.keys()):
                if now - _turn_states[k].get("last_activity", now) > 600.0:
                    _turn_states.pop(k, None)
        if len(_turn_states) > 100:
            oldest_keys = list(_turn_states.keys())[:30]
            for k in oldest_keys:
                _turn_states.pop(k, None)

        if turn_id not in _turn_states:
            _reset_turn_state(turn_id, session_id=session_id)

        st = _turn_states[turn_id]
        st["last_activity"] = now
        current_round = st.get("round_count", 1) or 1
        if "start_time" not in st:
            st["start_time"] = now
        elapsed_turn_time = now - st["start_time"]

    max_rounds = _get_setting("turn_max_tool_rounds", DEFAULT_MAX_ROUNDS)
    max_duration = float(_get_setting("turn_max_duration_seconds", DEFAULT_MAX_DURATION_S))

    tripped_rounds = current_round > max_rounds
    tripped_time = elapsed_turn_time >= max_duration

    if tripped_rounds or tripped_time:
        ALLOWED_TRIPPED_TOOLS = {"delegate_task", "process_manage", "skill_manage", "memory", "skill_view", "skills_list"}
        if tool_name in ALLOWED_TRIPPED_TOOLS:
            return None

        reason = f"[Circuit Breaker]: Quota reached (>{max_rounds} rounds or >{int(max_duration)}s). Heavy execution frozen. Delegate via delegate_task, record via skill_manage/memory, or finalize response."
        logger.warning(
            "proactive-agent: Tripped circuit breaker for tool %s (round=%s, elapsed=%.1fs)",
            tool_name, current_round, elapsed_turn_time
        )
        return {
            "action": "block",
            "message": reason,
            "reason": reason,
        }

    return None


def _post_tool_call(tool_name: str, args: Optional[dict] = None, result: Any = None, **hook_kwargs: Any) -> None:
    """Post-tool hook tracking accurate duration via duration_ms."""
    if not _should_govern(**hook_kwargs):
        return

    turn_id = hook_kwargs.get("turn_id") or ""
    if not turn_id:
        return

    duration_ms = hook_kwargs.get("duration_ms")
    dur_s = 0.0
    if duration_ms is not None:
        try:
            dur_s = float(duration_ms) / 1000.0
        except (ValueError, TypeError):
            dur_s = 0.0

    session_id = hook_kwargs.get("session_id") or ""
    now = time.monotonic()
    with _state_lock:
        if turn_id in _turn_states:
            st = _turn_states[turn_id]
            st["cumulative_duration_s"] = st.get("cumulative_duration_s", 0.0) + dur_s
            st["last_activity"] = now
        elif dur_s > 0:
            st = _reset_turn_state(turn_id, session_id=session_id)
            st["cumulative_duration_s"] = dur_s
            st["last_activity"] = now


def _transform_tool_result(tool_name: str, args: Optional[dict] = None, result: Any = None, **hook_kwargs: Any) -> Optional[str]:
    """Transform yielded terminal output into user/model guidance for proactive background handling."""
    if tool_name != "terminal":
        return None

    # Check if this thread triggered a timeout yield
    with _timeout_yield_lock:
        if threading.get_ident() not in _timeout_yield_tids:
            return None
        _timeout_yield_tids.discard(threading.get_ident())

    if not _should_govern(**hook_kwargs):
        return None

    if not result:
        return None

    try:
        data = json.loads(result) if isinstance(result, str) else result
        if not isinstance(data, dict):
            return None

        if data.get("status") == "yielded_to_background" or data.get("yielded_session_id"):
            session_id = data.get("yielded_session_id") or data.get("session_id", "background")
            timeout_val = _get_setting("terminal_auto_detach_seconds", 30)
            data["note"] = (
                f"[Foreground Command Timed Out (>{timeout_val}s) - Automatically Detached to Background]:\n"
                f"This command ran for more than {timeout_val} seconds in the foreground. To keep chat responsive, "
                f"it has been automatically detached to a background process (session_id: {session_id}) and continues running.\n"
                "[Action Required]:\n"
                "1. This command is now running asynchronously in the background. When it finishes, completion notification will be delivered automatically. Do NOT poll or sleep-wait for it;\n"
                "2. Conclude your turn immediately: write a concise text response informing the user that the command is running in the background, and release the conversation!"
            )
            logger.info("proactive-agent: Replaced yield note with custom timeout prompt for session %s", session_id)
            return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        logger.debug("proactive-agent: transform_tool_result parsing failed: %s", e)

    return None


def _on_session_clean(session_id: str = "", turn_id: str = "", **kwargs: Any) -> None:
    """Clear turn, task, and platform state on session reset or turn/session end."""
    _current_task_id.set("")
    _current_is_cron.set(False)
    _current_platform.set("")
    with _task_id_lock:
        if not session_id and not turn_id:
            _thread_task_ids.clear()
            _thread_is_cron.clear()
        else:
            _thread_task_ids.pop(threading.get_ident(), None)
            _thread_is_cron.pop(threading.get_ident(), None)
    with _state_lock:
        if not session_id and not turn_id:
            _thread_platforms.clear()
            _session_platforms.clear()
            _turn_platforms.clear()
            _turn_states.clear()
        else:
            _thread_platforms.pop(threading.get_ident(), None)
            if turn_id:
                _turn_platforms.pop(turn_id, None)
                _turn_states.pop(turn_id, None)
            if session_id:
                _session_platforms.pop(session_id, None)
                for k in list(_turn_states.keys()):
                    if _turn_states[k].get("session_id") == session_id or k.startswith(f"{session_id}:"):
                        _turn_states.pop(k, None)


def _on_system_prompt(session_info: Optional[Any] = None) -> str:
    """Dynamically render proactive agent discipline system prompt using current settings."""
    if not _should_govern(session_info=session_info):
        return ""

    detach_timeout = _get_setting("terminal_auto_detach_seconds", 30)
    max_rounds = _get_setting("turn_max_tool_rounds", DEFAULT_MAX_ROUNDS)
    max_duration = _get_setting("turn_max_duration_seconds", int(DEFAULT_MAX_DURATION_S))
    round_breaker = max_rounds + 1
    return f"""Role: Main dialogue orchestrator. Stay responsive; heavy execution never blocks the chat turn.

1. Triage & Dispatch-Then-Yield
- LIGHT (read files, status checks, quick probes): finish in <={max_rounds} tool rounds, then answer.
- HEAVY (multi-step edits, refactors, builds, test suites, training, commands likely >{detach_timeout}s):
  1. Scope minimally and aim to dispatch as early as possible, but MUST dispatch before tool round {max_rounds} or {max_duration}s:
     - Complex reasoning or code tasks: fan out via delegate_task.
     - Long shell commands: run terminal(background=True, notify=True).
  2. Release turn immediately: output a concise one-line status to the user and end your turn. NEVER run sleep or poll.

2. Hardware Governance Limits (Main Thread Only)
- Foreground command >{detach_timeout}s: auto-detached to background with completion notification. Do not wait; inform the user and end your turn.
- At tool round {round_breaker} (round >={round_breaker}) or >{max_duration}s turn time: terminal, patch, write_file and execution tools are frozen. Only delegate_task, process_manage (kill/list; wait is blocked), memory, skill_manage, skill_view, skills_list remain. Do not retry; delegate remaining work or deliver current state.

3. Event Wakeup & Physical Verification
- Asynchronous tasks wake the conversation automatically upon completion.
- Verify physical on-disk artifacts and real exit codes before answering. Deliver verified results directly — never make the user ask "is it done?".
- Never expose internal PIDs, session_ids, or raw commands. Keep output clean."""


_render_system_prompt_discipline = _on_system_prompt


def register(ctx: Any) -> None:
    """Register the proactive-agent plugin with Hermes."""
    global _ctx
    _ctx = ctx

    _install_adaptive_terminal_patch()
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
    ctx.register_hook("transform_tool_result", _transform_tool_result)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("pre_api_request", _on_pre_api_request)
    ctx.register_hook("agent_loop_stopped", _on_agent_loop_stopped)
    ctx.register_hook("on_session_reset", _on_session_clean)
    ctx.register_hook("on_session_end", _on_session_clean)

    try:
        ctx.register_system_prompt_section(
            "proactive-agent.discipline",
            _on_system_prompt,
            position="after_memory",
            max_chars=2000,
        )
    except Exception as e:
        logger.debug("proactive-agent: system prompt section skipped: %s", e)

    logger.info("proactive-agent plugin loaded successfully.")
