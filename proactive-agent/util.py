"""Small text and tool-result helpers."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

_CJK = re.compile(r"[\u3400-\u9fff]")


def is_cjk(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def short(text: Any, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: max(0, limit - 1)] + "…"


def first_line(text: Any, limit: int = 160) -> str:
    for line in str(text or "").splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return ""


def parse_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("\n") + 1:] if "\n" in s else s
    try:
        return json.loads(s)
    except Exception:
        start, end = s.find("{"), s.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(s[start:end + 1])
            except Exception:
                return None
    return None


def json_object(value: Any) -> Optional[dict]:
    """*value* as a dict when it IS a JSON object (or a fenced one), not when it merely quotes one."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip().startswith(("{", "```")):
        return None
    data = parse_json(value)
    return data if isinstance(data, dict) else None


def block(message: str) -> dict:
    """A pre_tool_call directive that refuses the call; *message* becomes the tool result."""
    return {"action": "block", "message": message, "reason": message}


def append_note(result: Any, note: str, key: str = "proactive_agent_note") -> str:
    """Attach a note to a tool result without breaking JSON results."""
    data = parse_json(result) if isinstance(result, str) else result
    if isinstance(data, dict) and (not isinstance(result, str) or result.strip().startswith("{")):
        data = dict(data)
        existing = data.get(key)
        data[key] = f"{existing}\n{note}" if existing else note
        return json.dumps(data, ensure_ascii=False, default=str)
    return f"{result}\n\n{note}"


def describe_call(tool_name: str, args: Optional[dict], limit: int = 120) -> str:
    args = args or {}
    for key in ("command", "code", "path", "url", "query", "pattern", "action", "goal"):
        if args.get(key):
            return f"{tool_name} {short(args[key], limit)}"
    return tool_name


def call_signature(tool_name: str, args: Optional[dict]) -> str:
    try:
        return tool_name + ":" + json.dumps(args or {}, ensure_ascii=False, sort_keys=True, default=str)[:2000]
    except Exception:
        return tool_name + ":" + str(args)[:2000]


def result_signature(result: Any) -> str:
    """First meaningful line of a tool result, with long numbers and hashes masked so that the
    same outcome seen twice compares equal despite timestamps and request ids."""
    data = parse_json(result) if isinstance(result, str) else result
    if isinstance(data, dict):
        head = first_line(data.get("error") or data.get("output") or data.get("stdout") or data.get("result"))
        if not head:
            head = short(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str), 160)
        code = data.get("exit_code", data.get("returncode"))
        if isinstance(code, int):
            head = f"[exit {code}] {head}"
    else:
        head = first_line(result)
    head = re.sub(r"\b[0-9a-f]{12,}\b", "#", head)
    return re.sub(r"\d{5,}", "#", head)
