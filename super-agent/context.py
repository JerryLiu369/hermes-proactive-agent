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


def forget_session(session_id: str) -> None:
    with _lock:
        _session_platforms.pop(session_id, None)


def reset_all() -> None:
    with _lock:
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


def _platform_is(name: str, **kw: Any) -> bool:
    return str(kw.get("platform") or info_get(kw.get("session_info"), "platform")).strip().lower() == name


def _cron_running() -> bool:
    """Hermes binds the cron run executing now in a contextvar; the gateway's cron owner re-binds the
    same one around its run."""
    try:
        from cron.execution_identity import current_cron_execution
        return current_cron_execution() is not None
    except Exception:
        return False


def is_cron(**kw: Any) -> bool:
    """Cron runs have no human waiting on them and are never governed. Hermes names a cron task
    ``cron:<job>:<run>`` and its session ``cron_<job>_<time>``."""
    ids = (kw.get("task_id"), kw.get("session_id"), kw.get("turn_id"), info_get(kw.get("session_info"), "session_id"))
    if any(str(v or "").startswith(("cron:", "cron_")) for v in ids) or _platform_is("cron", **kw):
        return True
    if _cron_running():
        return True
    flag = session_env("HERMES_CRON_SESSION") or os.environ.get("HERMES_CRON_SESSION", "")
    return flag.strip().lower() in ("1", "true", "yes", "on") or bool(os.environ.get("_HERMES_CRON_EXTERNAL_WORKER"))


def is_child(**kw: Any) -> bool:
    """A delegated subagent: Hermes gives it a ``sa-<n>-<hex>`` task id (``subagent-<n>-<hex>`` when no
    subagent id was minted) and the "subagent" platform."""
    return str(kw.get("task_id") or "").startswith(("sa-", "subagent-")) or _platform_is("subagent", **kw)


def is_governed_main(**kw: Any) -> bool:
    if is_child(**kw) or is_cron(**kw):
        return False
    platform = platform_for(**kw)
    return bool(platform) and platform in config.governed_platforms()
