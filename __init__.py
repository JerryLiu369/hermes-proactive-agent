"""
proactive-agent: Proactive Agent Governor Plugin for Hermes Agent (v2.1.0).

Features:
1. Terminal Adaptive Detach:
   Foreground terminal commands that run longer than `terminal_auto_detach_seconds` (default: 30s)
   are dynamically detached into tracked background processes using Hermes's native
   `yield_handler` / `process_registry.adopt_local` mechanism with completion notifications,
   releasing chat interaction immediately.

2. Tool Circuit Breaker & Subagent Enforcement:
   If the main agent in a single turn executes > `turn_max_tool_rounds` (default: 5 rounds,
   meaning when attempting round 6), OR if cumulative tool execution duration in that turn
   reaches `turn_max_duration_seconds` (default: 100s):
   - Heavy execution tools are blocked with an explicit directive.
   - Allowed tools: `delegate_task`, `skill_manage`, `memory`, `skill_view`, `skills_list`.
   - Forces the model to report phase progress and delegate heavy work to subagents.

3. Dynamic System Prompt Discipline:
   Dynamically renders prompt rules matching active config thresholds into session prompt context.

4. Resilient Human Intervention & Turn Lifecycle Recovery:
   - Resets round counts and elapsed timers when human intervention occurs
     (either via a new user turn or an out-of-band steer message mid-turn).
   - Auto-purges stale turn states to guarantee zero memory leaks across turns.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger("hermes.plugins.proactive_agent")

_ctx: Optional[Any] = None
_patched: bool = False
_state_lock = threading.Lock()

DEFAULT_MAX_ROUNDS = 5
DEFAULT_MAX_DURATION_S = 100.0

# Turn state: turn_id -> {
#   "session_id": str,
#   "turn_id": str,
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


def _get_setting(key: str, default: Any) -> Any:
    """Read a setting from env, then plugin config, then default."""
    env_key = f"PROACTIVE_{key.upper()}"
    if env_key in os.environ:
        try:
            return type(default)(os.environ[env_key])
        except Exception:
            pass

    if _ctx is not None:
        try:
            val = _ctx.get_config(key, None)
            if val is not None:
                return type(default)(val)
        except Exception:
            pass

    return default


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

        def _adaptive_wait_for_process(
            self,
            proc,
            timeout: int = 120,
            *,
            bounded_capture: bool = False,
            watch_interrupt_tid: Optional[int] = None,
            output=None,
            yield_handler: Optional[Callable] = None,
        ) -> dict:
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

        BaseEnvironment._wait_for_process = _adaptive_wait_for_process
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
    turn_id = hook_kwargs.get("turn_id") or ""
    session_id = hook_kwargs.get("session_id") or ""
    task_id = hook_kwargs.get("task_id") or ""
    if task_id.startswith("sa-"):
        return
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
    turn_id = hook_kwargs.get("turn_id") or ""
    task_id = hook_kwargs.get("task_id") or ""
    session_id = hook_kwargs.get("session_id") or ""
    if not turn_id or task_id.startswith("sa-"):
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
        else:
            _turn_states.clear()
        logger.info("proactive-agent: Cleared turn states on agent_loop_stopped for %s", session_key)


def _pre_tool_call(tool_name: str, args: Optional[dict], **hook_kwargs: Any) -> Optional[dict]:
    """Pre-tool hook enforcing circuit breaker (>5 rounds or >100s)."""
    turn_id = hook_kwargs.get("turn_id") or ""
    if not turn_id:
        return None

    task_id = hook_kwargs.get("task_id") or ""
    if task_id.startswith("sa-"):
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
        ALLOWED_TRIPPED_TOOLS = {"delegate_task", "skill_manage", "memory", "skill_view", "skills_list"}
        if tool_name in ALLOWED_TRIPPED_TOOLS:
            return None

        reason = "[Circuit Breaker]: Quota reached (>5 rounds or >100s). Heavy execution frozen. Delegate via delegate_task, record via skill_manage/memory, or finalize response."
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


def _post_tool_call(tool_name: str, args: Optional[dict], result: Any, **hook_kwargs: Any) -> None:
    """Post-tool hook tracking accurate duration via duration_ms."""
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
            _turn_states[turn_id] = {
                "session_id": session_id,
                "turn_id": turn_id,
                "start_time": now,
                "seen_api_requests": set(),
                "round_count": 1,
                "cumulative_duration_s": dur_s,
                "steer_count": 0,
                "last_activity": now,
            }


def _transform_tool_result(tool_name: str, args: Optional[dict], result: Any, **hook_kwargs: Any) -> Optional[str]:
    """Replace the default user-message yield note with an explicit timeout-detach directive in English."""
    if tool_name != "terminal":
        return None

    # Check whether this command was yielded due to our adaptive timer
    tid = threading.current_thread().ident
    is_timeout_yield = False
    with _timeout_yield_lock:
        if tid in _timeout_yield_tids:
            _timeout_yield_tids.discard(tid)
            is_timeout_yield = True

    if not is_timeout_yield:
        return None

    try:
        data = json.loads(result) if isinstance(result, str) else dict(result)
        if data.get("status") == "yielded_to_background" or data.get("yielded_session_id"):
            session_id = data.get("session_id") or data.get("yielded_session_id")
            timeout_val = _get_setting("terminal_auto_detach_seconds", 30)
            data["note"] = (
                f"[Foreground Command Timed Out (>{timeout_val}s) - Automatically Detached to Background]:\n"
                f"This command ran for more than {timeout_val} seconds in the foreground. To keep chat responsive, "
                f"it has been automatically detached to a background process (session_id: {session_id}) and continues running.\n"
                "[Action Required]:\n"
                "1. This command is now running asynchronously in the background. When it finishes, completion notification will be delivered automatically. Do NOT poll or sleep-wait for it;\n"
                "2. Conclude your turn immediately: write a concise text response informing the user that the command is running in the background (mention the session_id), and release the conversation!"
            )
            logger.info("proactive-agent: Replaced yield note with custom timeout prompt for session %s", session_id)
            return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        logger.debug("proactive-agent: transform_tool_result parsing failed: %s", e)

    return None


def _on_session_clean(session_id: str = "", turn_id: str = "", **kwargs: Any) -> None:
    """Clear turn state on session reset or turn/session end."""
    with _state_lock:
        if turn_id and turn_id in _turn_states:
            _turn_states.pop(turn_id, None)
        elif session_id:
            for k in list(_turn_states.keys()):
                if _turn_states[k].get("session_id") == session_id or k.startswith(f"{session_id}:"):
                    _turn_states.pop(k, None)
        else:
            _turn_states.clear()


def _render_system_prompt_discipline(session_info: Optional[Any] = None) -> str:
    """Dynamically render proactive agent discipline system prompt using current settings."""
    detach_timeout = _get_setting("terminal_auto_detach_seconds", 30)
    max_rounds = _get_setting("turn_max_tool_rounds", DEFAULT_MAX_ROUNDS)
    max_duration = _get_setting("turn_max_duration_seconds", int(DEFAULT_MAX_DURATION_S))
    round_breaker = max_rounds + 1
    return f"""## Execution Discipline: Proactive Orchestration & Asynchronous Heavy Tasks (重任务通通异步)

Own outcomes with initiative. Settle 1~{max_rounds} quick tool turns directly for lightweight inspections (reading files, status checks, minimal verification). Any non-trivial, iterative, or heavy work must be dispatched asynchronously:

### Proactive Orchestration Pillars:
1. **Reply first, then delegate**: When facing non-trivial work, give a concise one-line status update in turn 1 and release the turn immediately so the conversation stays responsive. Never freeze chat in sequential blocking tool loops or sleep waits.
2. **Embrace background execution & delegation**:
   - Proactively dispatch long-running commands to the background via `terminal(background=True, notify=True)` or `tmux` with an isolated cgroup.
   - Proactively decompose complex, multi-step, or multi-file tasks into discrete subtasks and fan them out in parallel to isolated subagents via `delegate_task`.
3. **Quiet execution**: Keep execution quiet. Never narrate executor IDs, internal PIDs, or raw background commands in chat.
4. **Verify before claiming done**: Verify actual physical artifacts on disk before declaring completion. Deliver final results and attachments via event-driven notifications.

### Automated Governor Safeguards:
- **Adaptive Terminal Detach (>{detach_timeout}s)**: Any foreground terminal command exceeding {detach_timeout}s is automatically detached into a tracked background process without being killed. Never poll or sleep-wait for it in the main thread.
- **Circuit Breaker (>{max_rounds} tool rounds or >{max_duration}s user-message processing time)**: When tool execution reaches round {round_breaker} or total processing time for the current user message reaches {max_duration}s, all monolithic execution tools (`terminal`, `patch`, `write_file`, etc.) are frozen. Heavy execution must be delegated via `delegate_task`. Lightweight self-evolution and memory tools (`skill_manage`, `memory`) remain permitted.
"""


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
            _render_system_prompt_discipline,
            position="after_memory",
            max_chars=2000,
        )
    except Exception as e:
        logger.debug("proactive-agent: system prompt section skipped: %s", e)

    logger.info("proactive-agent plugin loaded successfully.")
