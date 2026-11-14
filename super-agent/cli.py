"""``hermes super-agent status``: what the plugin is set to and what its ledger holds, for the person
running Hermes. It reads the same settings and ledger the gateway uses and changes nothing."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any, List

from . import config, ledger, preflight, util

_COMMAND = "super_agent_command"


def setup(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest=_COMMAND)
    status = sub.add_parser("status", help="Settings, host check and recent subagent tasks")
    status.add_argument("--limit", type=int, default=10, help="How many recent tasks to list (default 10)")
    status.add_argument("--json", action="store_true", help="Print JSON instead of text")


def _age(ts: Any) -> str:
    try:
        seconds = max(0.0, time.time() - float(ts))
    except (TypeError, ValueError):
        return "?"
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} d"


def _flags(task: dict) -> List[str]:
    flags = []
    if task.get("unreported"):
        flags.append("not heard by the user yet")
    if task.get("awaiting"):
        flags.append("question open")
    if task.get("files"):
        flags.append(f"{len(task['files'])} file(s) changed")
    return flags


def collect(limit: int = 10) -> dict:
    settings = {key: {"value": config.get(key), "default": default} for key, default in config.DEFAULTS.items()}
    try:
        issues = preflight.check_host()
    except Exception as e:
        issues = [f"host check failed: {e}"]
    try:
        tasks, where, error = ledger.latest(max(0, limit)), ledger.location(), ""
    except Exception as e:
        tasks, where, error = [], "", str(e) or type(e).__name__
    return {"ledger": where, "ledger_error": error, "settings": settings, "host_issues": issues,
            "tasks": [{k: t.get(k) for k in ("id", "parent", "status", "goal", "started", "ended", "files",
                                             "unreported", "awaiting", "question", "summary")} for t in tasks]}


def _text(info: dict, version: str) -> str:
    out = [f"super-agent {version}"]
    if info["ledger_error"]:
        out.append(f"Ledger:   unavailable ({info['ledger_error']})")
    else:
        out.append(f"Ledger:   {info['ledger'] or 'in memory (lost when Hermes stops)'}")
    out += ["", "Settings"]
    width = max(len(k) for k in info["settings"])
    for key, s in info["settings"].items():
        mark = "" if s["value"] == s["default"] else "   (changed)"
        out.append(f"  {key.ljust(width)}  {json.dumps(s['value'], ensure_ascii=False)}{mark}")
    out += ["", "Host check"]
    out += [f"  - {issue}" for issue in info["host_issues"]] or ["  no issues found"]
    tasks = info["tasks"]
    out += ["", "Recent subagent tasks (running first, then newest)" if tasks else "No subagent tasks recorded yet."]
    for t in tasks:
        when = (f"started {_age(t['started'])} ago" if t["status"] == ledger.RUNNING
                else f"ended {_age(t['ended'])} ago")
        out.append(f"  {str(t['status']).ljust(14)} {when.ljust(18)} \"{util.short(t['goal'], 70)}\"")
        details = _flags(t)
        if t.get("awaiting") and t.get("question"):
            details.append("asks: " + util.short(t["question"], 100))
        out.append(f"  {'':14} chat {util.short(t['parent'], 40)}" + ("; " + "; ".join(details) if details else ""))
    return "\n".join(out)


def make_handler(version: str):
    def handle(args: argparse.Namespace) -> int:
        command = getattr(args, _COMMAND, None) or "status"
        if command != "status":
            print(f"unknown command: {command}; try: hermes super-agent status")
            return 2
        info = collect(int(getattr(args, "limit", 10) or 0))
        if getattr(args, "json", False):
            print(json.dumps(dict(info, version=version), ensure_ascii=False, indent=2, default=str))
        else:
            print(_text(info, version))
        return 0
    return handle


def register(ctx: Any, version: str) -> None:
    ctx.register_cli_command("super-agent", "Hermes Super Agent: settings and recent subagent tasks", setup,
                             make_handler(version),
                             description="Show the plugin's settings, the startup host check and the subagent "
                                         "tasks in its ledger. Read only.")
