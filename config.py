"""Plugin settings: environment override, then plugin config, then the default below."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, List, Optional

logger = logging.getLogger("hermes.plugins.proactive_agent")

DEFAULTS: dict[str, Any] = {
    # Platforms whose main chat agent is governed. Unlisted platforms are untouched.
    "governed_platforms": ["weixin"],
    # Foreground terminal commands longer than this move to the background (0 disables).
    "terminal_auto_detach_seconds": 30,
    # Main-agent turn budget. Yellow = reminder appended to tool results; red = only
    # read-only tools and delegate_task stay available.
    "turn_soft_seconds": 60.0,
    "turn_hard_seconds": 120.0,
    "turn_soft_output_chars": 30000,
    "turn_hard_output_chars": 80000,
    # Handoff contract for delegate_task.
    "handoff_min_context_chars": 120,
    "handoff_summary_enabled": True,
    "handoff_summary_timeout_seconds": 12.0,
    "handoff_memory_max_chars": 4000,
    # Worker (subagent) guards.
    "worker_max_tool_rounds": 40,
    "worker_repeat_limit": 3,
    "worker_guard_risky_commands": True,
    "worker_write_guard": True,
    # Result verification and progress updates.
    "verify_child_results": True,
    "progress_push_enabled": True,
    "progress_interval_seconds": 300,
}

_ctx: Optional[Any] = None


def set_ctx(ctx: Any) -> None:
    global _ctx
    _ctx = ctx


def ctx() -> Optional[Any]:
    return _ctx


def _coerce(value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, list):
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        s = str(value).strip()
        if s.startswith("["):
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        return [x.strip() for x in s.split(",") if x.strip()]
    return type(default)(value)


def get(key: str) -> Any:
    default = DEFAULTS[key]
    env_key = f"PROACTIVE_{key.upper()}"
    if env_key in os.environ:
        try:
            return _coerce(os.environ[env_key], default)
        except Exception:
            logger.warning("proactive-agent: ignoring invalid %s=%r", env_key, os.environ[env_key])
    if _ctx is not None:
        try:
            value = _ctx.get_config(key, None)
            if value is not None:
                return _coerce(value, default)
        except Exception:
            logger.warning("proactive-agent: ignoring invalid config value for %s", key)
    return default


def governed_platforms() -> List[str]:
    return [p.lower() for p in get("governed_platforms")]
