"""
proactive-agent: runs the Hermes chat agent like a secretary that manages workers.

The main agent (the "secretary") keeps the chat responsive and does the thinking; delegated
subagents (the "workers") do the slow execution:

1. Turn budget: a reminder at the soft limit; at the hard limit only read-only tools and
   delegate_task stay available. Long foreground commands move to the background.
2. Handoff: a too-short delegate_task brief is returned once; accepted ones get the user's
   recent messages, memory, working rules and a result schema attached.
3. Workers: a stop after the same step gives the same result again and again (the step budget
   is Hermes' own delegation.max_iterations).
4. Results and progress: the secretary is told what to check when a worker returns, and the
   user gets periodic progress lines.

Only platforms listed in ``governed_platforms`` (default: weixin) are affected; cron runs and
workers spawned by ungoverned sessions are never touched.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from . import config, context, handoff, prompt, secretary, workers

__version__ = "0.1.13"

logger = logging.getLogger("hermes.plugins.proactive_agent")


def _worker_for(**kw: Any) -> Optional[dict]:
    if not context.is_child(**kw):
        return None
    return workers.lookup(task_id=str(kw.get("task_id") or ""), session_id=str(kw.get("session_id") or ""))


def _main_session(**kw: Any) -> Optional[str]:
    session_id = str(kw.get("session_id") or "")
    if session_id and context.is_governed_main(**kw):
        return session_id
    return None


def _guarded(fn):
    """A raising pre_tool_call hook blocks the tool in Hermes; never let a plugin bug do that."""
    def wrapper(*args: Any, **kwargs: Any):
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception("proactive-agent: %s failed; continuing without governance", fn.__name__)
            return None
    wrapper.__name__ = fn.__name__
    return wrapper


@_guarded
def _pre_llm_call(**kw: Any) -> Optional[dict]:
    session_id = _main_session(**kw)
    if not session_id:
        return None
    return secretary.on_turn_start(session_id, str(kw.get("turn_id") or ""), context.platform_for(**kw),
                                   kw.get("conversation_history"), kw.get("user_message"))


@_guarded
def _pre_api_request(**kw: Any) -> None:
    session_id = _main_session(**kw)
    if session_id:
        secretary.on_api_request(session_id, str(kw.get("turn_id") or ""),
                                 kw.get("request_messages") or kw.get("conversation_history"))


@_guarded
def _pre_tool_call(tool_name: str = "", args: Optional[dict] = None, **kw: Any) -> Optional[dict]:
    worker = _worker_for(**kw)
    if worker is not None:
        return workers.pre_tool_call(worker, tool_name, args, **kw)
    session_id = _main_session(**kw)
    if session_id:
        return secretary.pre_tool_call(session_id, str(kw.get("turn_id") or ""), tool_name, args)
    return None


@_guarded
def _post_tool_call(tool_name: str = "", args: Optional[dict] = None, result: Any = None, **kw: Any) -> None:
    worker = _worker_for(**kw)
    if worker is not None:
        workers.post_tool_call(worker, tool_name, args, result, **kw)


@_guarded
def _transform_tool_result(tool_name: str = "", args: Optional[dict] = None, result: Any = None,
                           **kw: Any) -> Optional[str]:
    worker = _worker_for(**kw)
    if worker is not None:
        return workers.transform_tool_result(worker, tool_name, result)
    session_id = _main_session(**kw)
    if session_id:
        return secretary.transform_tool_result(session_id, tool_name, result)
    return None


def _parent_governed(parent_session: str) -> bool:
    return bool(parent_session) and context.is_governed_main(session_id=parent_session)


@_guarded
def _subagent_start(**kw: Any) -> None:
    workers.on_subagent_start(_parent_governed, secretary.language, **kw)


@_guarded
def _subagent_stop(**kw: Any) -> None:
    workers.on_subagent_stop(**kw)


@_guarded
def _on_turn_end(session_id: str = "", **kw: Any) -> None:
    # Hermes fires on_session_end after every run_conversation, so only per-turn state goes here;
    # workers, pending result notes and progress timers must outlive the turn.
    secretary.forget(session_id)


@_guarded
def _on_session_clean(session_id: str = "", **kw: Any) -> None:
    secretary.forget(session_id)
    handoff.forget(session_id)
    workers.forget_parent(session_id)
    context.forget_session(session_id)


def register(ctx: Any) -> None:
    config.set_ctx(ctx)
    secretary.install_detach_patch()
    hooks = {
        "pre_llm_call": _pre_llm_call,
        "pre_api_request": _pre_api_request,
        "pre_tool_call": _pre_tool_call,
        "post_tool_call": _post_tool_call,
        "transform_tool_result": _transform_tool_result,
        "subagent_start": _subagent_start,
        "subagent_stop": _subagent_stop,
        "on_session_end": _on_turn_end,
        "on_session_reset": _on_session_clean,
        "on_session_finalize": _on_session_clean,
    }
    for name, fn in hooks.items():
        ctx.register_hook(name, fn)
    try:
        ctx.register_system_prompt_section("proactive-agent.discipline", prompt.render,
                                           position="after_memory", max_chars=2000)
    except Exception as e:
        logger.debug("proactive-agent: system prompt section skipped: %s", e)
    logger.info("proactive-agent %s loaded", __version__)
