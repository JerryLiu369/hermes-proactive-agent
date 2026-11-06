"""
proactive-agent: runs the Hermes chat agent like a secretary with a team of workers.

The main agent stays reachable and decides; delegated subagents do the slow work. The plugin
keeps four promises, one module each:

1. The chat stays responsive (secretary): a turn time budget, with a note at the soft limit and
   editing and execution paused at the hard limit, and long terminal commands moved to the
   background.
2. Subagents start informed (handoff): a thin or clashing delegate_task brief is returned once;
   an accepted one carries the user's words, memory, running and unfinished tasks, working rules
   and a result schema.
3. Subagents do not spin or guess (workers, handoff, reports): a subagent that gets the same
   result from the same step again and again is stopped; one that needs a decision it should not make, or
   is refused by approval or a rule, ends with a question that the main agent answers or takes
   to the user.
4. Results are checked, reported and remembered (reports, ledger): the main agent is told what
   to verify when a subagent returns, a result stays unreported until the user has heard it (a
   user message that cuts into the report gets it repeated), and a persistent ledger records
   every task, survives restarts and briefs the next one.

Only platforms listed in ``governed_platforms`` (default: weixin) and the subagents they spawn
are governed; cron runs are never touched.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from . import config, context, handoff, prompt, reports, secretary, workers

__version__ = "0.1.15"

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
    turn_id = str(kw.get("turn_id") or "")
    secretary.on_turn_start(session_id, turn_id, context.platform_for(**kw),
                            kw.get("conversation_history"), kw.get("user_message"))
    turn = secretary.turn_facts(session_id, turn_id)
    notes = reports.pending_notes(session_id, turn["key"] if turn else turn_id, kw.get("user_message"),
                                  by_user=secretary.by_user(kw.get("user_message")))
    return {"context": notes} if notes else None


@_guarded
def _pre_api_request(**kw: Any) -> None:
    session_id = _main_session(**kw)
    if session_id:
        # conversation_history is the live turn with the user's own words; the request copy has
        # them rewritten (plugin context appended, redirects wrapped in Hermes' scaffold).
        secretary.on_api_request(session_id, str(kw.get("turn_id") or ""),
                                 kw.get("conversation_history") or kw.get("request_messages"))


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
    elif tool_name == "delegate_task" and _main_session(**kw):
        reports.record_dispatch(result)


@_guarded
def _transform_tool_result(tool_name: str = "", args: Optional[dict] = None, result: Any = None,
                           **kw: Any) -> Optional[str]:
    worker = _worker_for(**kw)
    if worker is not None:
        return workers.transform_tool_result(worker, tool_name, args, result)
    session_id = _main_session(**kw)
    if not session_id:
        return None
    out = secretary.transform_tool_result(session_id, tool_name, result)
    noted = reports.tool_result_note(session_id, secretary.turn_facts(session_id, str(kw.get("turn_id") or "")),
                                     result if out is None else out)
    return out if noted is None else noted


def _parent_governed(parent_session: str) -> bool:
    return bool(parent_session) and context.is_governed_main(session_id=parent_session)


@_guarded
def _subagent_start(**kw: Any) -> None:
    workers.on_subagent_start(_parent_governed, **kw)


@_guarded
def _subagent_stop(**kw: Any) -> None:
    reports.on_subagent_stop(**kw)


@_guarded
def _on_turn_end(session_id: str = "", **kw: Any) -> None:
    # Hermes fires on_session_end after every run_conversation, so only per-turn state goes here;
    # workers and pending result notes must outlive the turn.
    if not session_id:
        return
    if _main_session(session_id=session_id, **kw):
        reports.on_turn_end(session_id, secretary.turn_facts(session_id, str(kw.get("turn_id") or "")),
                            completed=bool(kw.get("completed")))
    secretary.forget(session_id)
    handoff.forget(session_id)
    reports.forget(session_id)


@_guarded
def _on_session_clean(session_id: str = "", old_session_id: str = "", **kw: Any) -> None:
    # /new reports the fresh session as session_id and the one that ended as old_session_id.
    # The ledger is kept: a gateway shutdown fires this too, and restart recovery reads it.
    ended = old_session_id or session_id
    if not ended:
        return
    secretary.forget(ended)
    handoff.forget(ended)
    reports.forget(ended)
    workers.forget_parent(ended)
    context.forget_session(ended)


def _reset_all() -> None:
    """Drop all in-memory state (tests)."""
    secretary.reset_all()
    handoff.reset_all()
    reports.reset_all()
    workers.reset_all()
    context.reset_all()


_HOOKS = {
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


def register(ctx: Any) -> None:
    config.set_ctx(ctx)
    try:
        reports.recover_lost()
    except Exception:
        logger.exception("proactive-agent: task ledger unavailable")
    secretary.install_detach_patch()
    for name, fn in _HOOKS.items():
        ctx.register_hook(name, fn)
    try:
        ctx.register_system_prompt_section("proactive-agent.discipline", prompt.render,
                                           position="after_memory", max_chars=2000)
    except Exception as e:
        logger.debug("proactive-agent: system prompt section skipped: %s", e)
    logger.info("proactive-agent %s loaded", __version__)
