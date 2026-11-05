"""Who is behind a hook call: the governed main chat agent, a delegated child, or a cron run."""

from __future__ import annotations

import collections.abc
import os
import threading
from typing import Any, Dict

from . import config

_lock = threading.Lock()
_session_platforms: Dict[str, str] = {}


def info_get(info: Any, key: str) -> str:
    if info is None:
        return ""
    if isinstance(info, collections.abc.Mapping):
        return str(info.get(key) or "")
    getter = getattr(info, "get", None)
    if callable(getter):
        try:
            return str(getter(key) or "")
        except Exception:
            pass
    return str(getattr(info, key, "") or "")


def session_env(name: str) -> str:
    """Hermes binds session facts in contextvars; hook workers run in a copy of that context."""
    try:
        from gateway.session_context import get_session_env
        return get_session_env(name, "") or ""
    except Exception:
        return os.environ.get(name, "")


def remember_platform(session_id: str, platform: str) -> None:
    if session_id and platform:
        with _lock:
            _session_platforms[session_id] = platform.strip().lower()


def forget_session(session_id: str = "") -> None:
    with _lock:
        if session_id:
            _session_platforms.pop(session_id, None)
        else:
            _session_platforms.clear()


def platform_for(**kw: Any) -> str:
    platform = kw.get("platform") or info_get(kw.get("session_info"), "platform")
    if platform:
        return str(platform).strip().lower()
    session_id = kw.get("session_id") or info_get(kw.get("session_info"), "session_id")
    if session_id:
        with _lock:
            known = _session_platforms.get(session_id)
        if known:
            return known
    return (session_env("HERMES_SESSION_PLATFORM") or os.environ.get("HERMES_PLATFORM", "")).strip().lower()


def _looks_cron(value: str) -> bool:
    return bool(value) and (value == "cron" or value.startswith(("cron:", "cron_")) or ":cron:" in value)


def is_cron(**kw: Any) -> bool:
    """Cron runs have no human waiting on them and are never governed."""
    session_info = kw.get("session_info")
    for value in (kw.get("task_id") or "", kw.get("session_id") or "", kw.get("turn_id") or "",
                  info_get(session_info, "session_id")):
        if _looks_cron(str(value)):
            return True
    if (kw.get("platform") or "") == "cron" or info_get(session_info, "platform") == "cron":
        return True
    ctx = config.ctx()
    if ctx is not None and hasattr(ctx, "current_cron_execution"):
        try:
            if ctx.current_cron_execution() is not None:
                return True
        except Exception:
            pass
    try:
        from cron.execution_identity import current_cron_execution
        if current_cron_execution() is not None:
            return True
    except Exception:
        pass
    try:
        from gateway.session_cron import current_execution
        if current_execution() is not None:
            return True
    except Exception:
        pass
    if session_env("HERMES_CRON_SESSION") == "1":
        return True
    return os.environ.get("HERMES_CRON_SESSION") == "1" or bool(os.environ.get("_HERMES_CRON_EXTERNAL_WORKER"))


def is_child(**kw: Any) -> bool:
    task_id = str(kw.get("task_id") or "")
    if task_id.startswith(("sa-", "subagent-")):
        return True
    if (kw.get("platform") or "") == "subagent" or info_get(kw.get("session_info"), "platform") == "subagent":
        return True
    try:
        from agent.delegation_context import is_delegated_child_context
        return bool(is_delegated_child_context())
    except Exception:
        return False


def is_governed_main(**kw: Any) -> bool:
    if is_child(**kw) or is_cron(**kw):
        return False
    platform = platform_for(**kw)
    return bool(platform) and platform in config.governed_platforms()
