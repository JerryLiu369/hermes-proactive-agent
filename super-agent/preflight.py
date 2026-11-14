"""Startup host-capability self-check (zero model calls, reported once to the main agent)."""

from __future__ import annotations

import logging
import threading
from typing import Any, List, Optional

from . import config, ledger

logger = logging.getLogger("hermes.plugins.super_agent")

_MAX_ITERATIONS_THRESHOLD = 120
_RECOMMENDED_MAX_ITERATIONS = 90

_lock = threading.Lock()
_staged: bool = False
_checked: bool = False
_ledger_error: Optional[str] = None
_pending_notice: Optional[str] = None


def _has_platform_display_override(user_config: Any, platform_key: str, setting: str) -> bool:
    display = user_config.get("display") if isinstance(user_config, dict) else None
    if not isinstance(display, dict):
        return False
    platforms = display.get("platforms")
    if not isinstance(platforms, dict):
        return False
    platform_cfg = platforms.get(platform_key)
    return isinstance(platform_cfg, dict) and setting in platform_cfg


def _interim_enabled(user_config: Any, platform_key: str, resolve_display_setting: Any) -> bool:
    # Mirrors gateway/run_turn.py: Mattermost suppresses interim assistant messages unless
    # display.platforms.mattermost.interim_assistant_messages is explicitly configured.
    if platform_key == "mattermost" and not _has_platform_display_override(
        user_config, platform_key, "interim_assistant_messages"
    ):
        return False
    value = resolve_display_setting(user_config, platform_key, "interim_assistant_messages", True)
    if isinstance(value, str) and value.strip().lower() == "generic":
        return False
    return bool(value)


def check_host(ledger_error: Optional[str] = None) -> List[str]:
    """Check host capabilities once at startup without calling any model. Returns issue strings."""
    issues: List[str] = []

    # 1. Task ledger persistence
    if ledger_error:
        issues.append(
            f"Task ledger failed to open ({ledger_error}); subagent task history and crash recovery are unavailable."
        )
    elif ledger.is_memory_fallback():
        issues.append(
            "Task ledger could not write to plugin-data/super-agent/ledger.db and fell back to :memory:; "
            "task history and crash recovery will not survive a gateway restart."
        )

    # 2 & 3. Host config checks using Hermes' own resolvers (degrade safely if Hermes is not importable).
    try:
        from gateway.display_config import resolve_display_setting
        from hermes_cli.config_effective import load_user_config_effective
        from tools.delegate_tool_config import gateway_delegation_inline
    except Exception as e:
        logger.debug("super-agent: skipping host config preflight (Hermes modules unavailable: %s)", e)
        return issues

    try:
        from hermes_cli.config_defaults import DEFAULT_CONFIG
        default_max_iter = int((DEFAULT_CONFIG.get("delegation") or {}).get("max_iterations") or 250)
    except Exception:
        default_max_iter = 250

    try:
        user_config = load_user_config_effective()
    except Exception as e:
        logger.debug("super-agent: could not load effective user config for preflight: %s", e)
        return issues

    # 2. Check whether governed platforms resolve delegate_task to inline (blocking) mode.
    for platform_key in config.governed_platforms():
        try:
            interim_enabled = _interim_enabled(user_config, platform_key, resolve_display_setting)
            if gateway_delegation_inline(user_config, platform_key, interim_enabled=interim_enabled):
                issues.append(
                    f"Governed platform '{platform_key}' resolves delegate_task to synchronous inline mode "
                    f"(blocking the chat turn). Set delegation.gateway_mode: background or enable "
                    f"display.interim_assistant_messages (or display.platforms.{platform_key}."
                    f"interim_assistant_messages: true) in ~/.hermes/config.yaml."
                )
        except Exception as e:
            logger.debug("super-agent: delegation mode check failed for %s: %s", platform_key, e)

    # 3. Check whether delegation.max_iterations is unset (defaults to 250) or notably high (> 120).
    delegation_cfg = user_config.get("delegation") if isinstance(user_config, dict) else None
    raw_max_iter = delegation_cfg.get("max_iterations") if isinstance(delegation_cfg, dict) else None
    if raw_max_iter is None:
        max_iter = default_max_iter
        is_unset = True
    else:
        try:
            max_iter = int(raw_max_iter)
            is_unset = False
        except (TypeError, ValueError):
            max_iter = default_max_iter
            is_unset = True

    if max_iter > _MAX_ITERATIONS_THRESHOLD:
        if is_unset:
            issues.append(
                f"delegation.max_iterations is unset (Hermes defaults to {max_iter}); recommend setting "
                f"delegation.max_iterations: {_RECOMMENDED_MAX_ITERATIONS} in ~/.hermes/config.yaml to bound "
                f"worker subagent steps."
            )
        else:
            issues.append(
                f"delegation.max_iterations is {max_iter} (> {_MAX_ITERATIONS_THRESHOLD}); recommend lowering "
                f"it to ~{_RECOMMENDED_MAX_ITERATIONS} in ~/.hermes/config.yaml to bound worker subagent steps."
            )

    return issues


def _format_notice(issues: List[str]) -> Optional[str]:
    if not issues:
        return None
    return (
        "[super-agent] Startup host self-check found potential configuration issues "
        "(tell the user briefly once):\n" + "\n".join(f"- {item}" for item in issues)
    )


def stage_startup(ledger_error: Optional[str] = None) -> None:
    """Arm the one-time startup check without touching Hermes config locks during plugin load."""
    global _staged, _checked, _ledger_error, _pending_notice
    with _lock:
        _staged = True
        _checked = False
        _ledger_error = ledger_error
        _pending_notice = None


def run(ledger_error: Optional[str] = None) -> Optional[str]:
    """Run startup checks once and stage a single-delivery notice if any issues are found."""
    global _staged, _checked, _ledger_error, _pending_notice
    issues = check_host(ledger_error=ledger_error)
    notice = _format_notice(issues)
    with _lock:
        _staged = False
        _checked = True
        _ledger_error = ledger_error
        _pending_notice = notice
    if notice:
        logger.warning("super-agent: startup host self-check found %d issue(s)", len(issues))
    return notice


def pop_notice() -> Optional[str]:
    """Return and clear the staged startup notice (thread-safe, evaluated and delivered at most once)."""
    global _staged, _checked, _pending_notice
    with _lock:
        if _staged and not _checked:
            _checked = True
            _staged = False
            err = _ledger_error
            try:
                issues = check_host(ledger_error=err)
            except Exception:
                logger.exception("super-agent: startup preflight failed")
                issues = []
            notice = _format_notice(issues)
            if notice:
                logger.warning("super-agent: startup host self-check found %d issue(s)", len(issues))
            return notice
        notice = _pending_notice
        _pending_notice = None
        return notice


def reset_all() -> None:
    global _staged, _checked, _ledger_error, _pending_notice
    with _lock:
        _staged = False
        _checked = False
        _ledger_error = None
        _pending_notice = None
