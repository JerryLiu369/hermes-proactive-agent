"""Command classification and small text helpers shared by the main-agent and worker guards."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, List, Optional, Set

# Searches and scans that can hang for minutes and flood the context: blocked for everyone governed.
_STATE_DIR = r"(?:~|\$HOME|\$\{HOME\}|/home/[^/\s'\"]+|/root)/\.hermes\b"
_BROAD_ROOT = r"(?:/|~|\$HOME|\$\{HOME\}|/home|/home/[^/\s'\"]+|/root)/?"
_SCAN_PATTERNS = [
    (re.compile(r"\b(?:nmap|masscan|zmap|rustscan)\b"), "port scanning"),
    (re.compile(r"\bgrep\b[^|;&\n]*\s-[a-zA-Z]*[rR][a-zA-Z]*\b[^|;&\n]*(?:\s|['\"=])" + _STATE_DIR),
     "recursive grep over the Hermes state directory"),
    (re.compile(r"\b(?:rg|ag|ack)\b[^|;&\n]*(?:\s|['\"=])" + _STATE_DIR), "recursive search over the Hermes state directory"),
    (re.compile(r"\bgrep\b[^|;&\n]*\s-[a-zA-Z]*[rR][a-zA-Z]*\b[^|;&\n]*\s['\"]?" + _BROAD_ROOT + r"['\"]?(?:\s|$)"),
     "recursive grep over a home or root directory"),
    (re.compile(r"\b(?:rg|ag|ack)\b[^|;&\n]*\s['\"]?" + _BROAD_ROOT + r"['\"]?(?:\s|$)"),
     "recursive search over a home or root directory"),
    (re.compile(r"\bfind\s+['\"]?" + _BROAD_ROOT + r"['\"]?\s(?![^|;&\n]*-maxdepth)"), "unbounded find over a home or root directory"),
]
_PORT_LOOP_CONNECT = re.compile(r"connect_ex|/dev/tcp/|\bnc\s+-[a-zA-Z]*z")
_PORT_LOOP_RANGE = re.compile(r"range\(\s*\d+\s*,\s*\d{3,}|\bseq\s+\d+\s+\d{3,}|\{\d+\.\.\d{3,}\}")

# Changes that can lock the user out or destroy state. Workers need an explicit rollback in the handoff.
_RISKY_PATTERNS = [
    (re.compile(r"sshd?_config|ssh\.socket|sshd\.service"), "SSH server configuration"),
    (re.compile(r"\bsystemctl\s+(?:restart|stop|disable|mask|reload)\s+(?:ssh|sshd)(?:\.\w+)?\b"), "restarting or stopping SSH"),
    (re.compile(r"\b(?:iptables|ip6tables|nft|ufw|firewall-cmd)\b"), "firewall rules"),
    (re.compile(r"-X\s*['\"]?DELETE|--request\s+['\"]?DELETE|requests\.delete\(|method\s*=\s*['\"]DELETE"),
     "HTTP DELETE against an API"),
    (re.compile(r"\brm\s+-[a-zA-Z]*(?:rf|fr)[a-zA-Z]*\s+['\"]?(?:/|~|\$HOME)(?:\s|$|['\"]|etc\b|home\b|var\b|usr\b|root\b|\.hermes\b|opt\b)"),
     "recursive delete of a system or home path"),
    (re.compile(r"\bterraform\s+destroy\b|\bpulumi\s+destroy\b"), "infrastructure destroy"),
    (re.compile(r"\b(?:oci|aws|gcloud|az|doctl|hcloud)\b[^\n|;&]*\b(?:terminate|delete|destroy)\b"), "cloud resource deletion"),
    (re.compile(r"\bwrangler\b[^\n|;&]*\bdelete\b"), "Cloudflare resource deletion"),
    (re.compile(r"\bkubectl\s+delete\b"), "Kubernetes deletion"),
    (re.compile(r"\bdocker\s+(?:system\s+prune|volume\s+(?:rm|prune))\b"), "Docker volume removal"),
    (re.compile(r"\bmkfs\b|\bdd\s+[^\n]*of=/dev/"), "disk formatting"),
    (re.compile(r"\b(?:shutdown|reboot|poweroff|halt)\b(?!\s*\()"), "machine shutdown or reboot"),
    (re.compile(r"\bDROP\s+(?:TABLE|DATABASE)\b", re.IGNORECASE), "dropping database objects"),
]

# Words in a handoff that mean the task itself touches risky ground.
_RISKY_TASK_WORDS = re.compile(
    r"sshd|ssh\.socket|ssh\s*(?:端口|port)|改.{0,6}ssh|iptables|ufw|firewall|防火墙|"
    r"(?:删除|删掉|解绑|移除).{0,12}(?:dns|记录|域名|实例|数据库|服务器|存储桶)|"
    r"\b(?:delete|remove|unbind)\b.{0,24}\b(?:dns|record|domain|instance|database|bucket|server)s?\b|"
    r"\bterminate\b|\bdestroy\b|销毁|重建实例|drop\s+table|rm\s+-rf|删库|reinstall|重装系统",
    re.IGNORECASE,
)
_ROLLBACK_WORDS = re.compile(
    r"rollback|roll\s*back|回滚|回退|fallback|恢复方案|备份|backup|snapshot|快照|"
    r"保留.{0,16}(?:22|旧|原|现有)|keep\s.{0,40}(?:open|working|until|alive)|until\s.{0,40}verified",
    re.IGNORECASE,
)

_TARGET_RE = re.compile(
    r"(?:~|/)[\w.\-]+(?:/[\w.\-]+)+"  # paths with at least two segments
    r"|\b(?:\d{1,3}\.){3}\d{1,3}\b"  # IPv4
    r"|\b[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\.(?:com|net|org|io|dev|top|cn|app|xyz|cloud|ai|me|co|site)\b",
    re.IGNORECASE,
)
_GENERIC_TARGETS = {"/home/ubuntu", "~/.hermes", "/tmp", "/usr/bin", "/usr/local", "/etc", "/var/log"}

_CJK = re.compile(r"[\u3400-\u9fff]")


def command_of(tool_name: str, args: Optional[dict]) -> str:
    args = args or {}
    if tool_name == "terminal":
        return str(args.get("command") or "")
    if tool_name == "execute_code":
        return str(args.get("code") or "")
    return ""


def scan_reason(command: str) -> Optional[str]:
    if not command:
        return None
    for pattern, reason in _SCAN_PATTERNS:
        if pattern.search(command):
            return reason
    if _PORT_LOOP_CONNECT.search(command) and _PORT_LOOP_RANGE.search(command):
        return "port scanning loop"
    return None


def risky_reason(command: str) -> Optional[str]:
    if not command:
        return None
    for pattern, reason in _RISKY_PATTERNS:
        if pattern.search(command):
            return reason
    return None


def task_is_risky(text: str) -> bool:
    return bool(_RISKY_TASK_WORDS.search(text or "")) or risky_reason(text or "") is not None


def has_rollback(text: str) -> bool:
    return bool(_ROLLBACK_WORDS.search(text or ""))


def targets_in(text: str) -> Set[str]:
    found = set()
    for match in _TARGET_RE.findall(text or ""):
        target = match.strip().rstrip(".,;:)'\"").lower()
        if len(target) >= 6 and target not in _GENERIC_TARGETS:
            found.add(target)
    return found


def is_cjk(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def short(text: Any, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: max(0, limit - 1)] + "…"


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


def append_note(result: Any, note: str, key: str = "proactive_agent_note") -> str:
    """Attach a note to a tool result without breaking JSON results."""
    if isinstance(result, str):
        data = None
        try:
            data = json.loads(result)
        except Exception:
            pass
        if isinstance(data, dict):
            existing = data.get(key)
            data[key] = f"{existing}\n{note}" if existing else note
            return json.dumps(data, ensure_ascii=False)
        return f"{result}\n\n{note}"
    try:
        if isinstance(result, dict):
            data = dict(result)
            data[key] = note
            return json.dumps(data, ensure_ascii=False)
    except Exception:
        pass
    return f"{result}\n\n{note}"


def first_line(text: str, limit: int = 160) -> str:
    for line in str(text or "").splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return ""


def outcome_of(result: Any) -> tuple[bool, str]:
    """(failed, signature) for a tool result. The signature ignores timestamps and ids so that
    the same failure seen twice compares equal."""
    data = parse_json(result) if isinstance(result, str) else result
    failed = False
    head = ""
    if isinstance(data, dict):
        error = data.get("error")
        exit_code = data.get("exit_code", data.get("returncode"))
        status = str(data.get("status") or "").lower()
        if error:
            failed = True
            head = first_line(str(error))
        if isinstance(exit_code, int) and exit_code not in (0,):
            failed = True
        if status in ("error", "failed", "failure"):
            failed = True
        if not head:
            head = first_line(str(data.get("output") or data.get("stdout") or data.get("result") or ""))
        if not head:
            head = short(json.dumps(data, ensure_ascii=False, sort_keys=True), 160)
        if isinstance(exit_code, int):
            head = f"[exit {exit_code}] {head}"
    else:
        head = first_line(str(result or ""))
    head = re.sub(r"\b[0-9a-f]{12,}\b", "#", head)
    head = re.sub(r"\d{5,}", "#", head)
    return failed, head


def call_signature(tool_name: str, args: Optional[dict]) -> str:
    try:
        return tool_name + ":" + json.dumps(args or {}, ensure_ascii=False, sort_keys=True)[:2000]
    except Exception:
        return tool_name + ":" + str(args)[:2000]


def describe_call(tool_name: str, args: Optional[dict], limit: int = 120) -> str:
    command = command_of(tool_name, args)
    if command:
        return f"{tool_name} `{short(command, limit)}`"
    args = args or {}
    for key in ("path", "url", "query", "pattern", "action", "goal"):
        if args.get(key):
            return f"{tool_name} {short(args[key], limit)}"
    return tool_name


def any_overlap(a: Iterable[str], b: Iterable[str]) -> List[str]:
    b_set = set(b)
    hits = []
    for x in a:
        for y in b_set:
            if x == y or x.startswith(y + "/") or y.startswith(x + "/"):
                hits.append(x if len(x) >= len(y) else y)
    return sorted(set(hits))
