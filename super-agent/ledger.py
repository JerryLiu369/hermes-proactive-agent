"""Task ledger: one row per delegated task, shared by the main agent and its workers.

Only the plugin's hooks write it, and no model is called. It lives in Hermes' plugin-data
directory, so a gateway restart keeps what was running, which files each task changed, how it
ended, whether the user has heard the result yet and whether its question is still open.
The main agent reads it through result and restart notes, which wait in the ledger until that
chat's next turn; workers read it through their handoff briefing.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, List, Optional

logger = logging.getLogger("hermes.plugins.super_agent")

PLUGIN_NAME = "super-agent"
RUNNING = "running"
LOST = "lost"
STOPPED = "stopped"

_KEEP_SECONDS = 7 * 86400.0
# Same horizon as the in-memory worker registry: a record older than this whose subagent_stop
# never arrived is no longer treated as running.
_RUNNING_HORIZON_S = 6 * 3600.0
_KEEP_FINISHED_PER_PARENT = 20
# A result the user has not heard about after a day is history, not news.
_REPORT_HORIZON_S = 86400.0
_MAX_FILES = 20
_MAX_STEERS = 5
_COLUMNS = ("id", "parent", "child", "status", "started", "ended", "pid")

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, parent TEXT NOT NULL, child TEXT NOT NULL DEFAULT '', "
    "status TEXT NOT NULL, started REAL NOT NULL, ended REAL, pid INTEGER NOT NULL, data TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS tasks_parent ON tasks (parent, status)",
    "CREATE INDEX IF NOT EXISTS tasks_child ON tasks (child)",
    "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, parent TEXT NOT NULL, "
    "created REAL NOT NULL, text TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS notes_parent ON notes (parent)",
)

_lock = threading.RLock()
_db: Optional[sqlite3.Connection] = None
_memory_fallback: bool = False


def _is_memory_db(db: sqlite3.Connection) -> bool:
    try:
        rows = db.execute("PRAGMA database_list").fetchall()
        return not any(row[1] == "main" and bool(row[2]) for row in rows)
    except Exception:
        return True


def is_memory_fallback() -> bool:
    with _lock:
        return _memory_fallback


def reset_all() -> None:
    global _memory_fallback
    with _lock:
        _memory_fallback = False


def _open_default() -> sqlite3.Connection:
    try:
        from plugins.plugin_storage import plugin_db
        return plugin_db(PLUGIN_NAME, "ledger.db")
    except Exception:
        pass
    try:
        from hermes_constants import get_hermes_home
        root = Path(get_hermes_home()) / "plugin-data" / PLUGIN_NAME
        root.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(root / "ledger.db"), check_same_thread=False)
    except Exception as e:
        logger.debug("super-agent: no plugin-data directory (%s); task ledger kept in memory", e)
    return sqlite3.connect(":memory:", check_same_thread=False)


def _alive(pid: int) -> bool:
    """Whether a process still runs. Unsure means alive, so a live gateway's tasks are never marked lost."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
            finally:
                kernel32.CloseHandle(handle)
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except Exception:
        return True
    return True


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except Exception:
        return ""


def _proc_identity(pid: int) -> str:
    """``<boot id>:<start time>`` of a process on Linux, "" elsewhere or when it is gone. A PID alone is not
    enough: a restarted container gives the gateway the same PID, and after a reboot an old PID may belong to
    any process."""
    boot = _boot_id()
    if not boot or pid <= 0:
        return ""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
        return f"{boot}:{stat[stat.rfind(')') + 2:].split()[19]}"
    except Exception:
        return ""


def _still_running(task: dict) -> bool:
    """Whether the Hermes process that started *task* still runs. Unsure means yes."""
    pid, proc = int(task.get("pid") or 0), str(task.get("proc") or "")
    if proc:
        boot = _boot_id()
        if boot and proc.split(":", 1)[0] != boot:
            return False  # the machine rebooted since
        current = _proc_identity(pid)
        if current:
            return current == proc
    elif pid == os.getpid():
        return True
    return _alive(pid)


def load(path: Optional[Path] = None) -> List[dict]:
    """Open the ledger and return the tasks a Hermes process that has since exited left running.
    Those are marked lost: Hermes does not resume a child across a restart."""
    global _db, _memory_fallback
    with _lock:
        if _db is not None:
            try:
                _db.close()
            except Exception:
                pass
        _db = sqlite3.connect(str(path), check_same_thread=False) if path else _open_default()
        _memory_fallback = _is_memory_db(_db) if path is None else False
        for statement in _SCHEMA:
            _db.execute(statement)
        _db.commit()
        now = time.time()
        _expire(_db, now)
        _db.commit()
        lost = []
        for t in _select("status = ?", (RUNNING,)):
            if _still_running(t):
                continue
            # Conditional, so of two processes starting together only one claims (and reports) the task.
            claimed = _db.execute("UPDATE tasks SET status = ?, ended = ? WHERE id = ? AND status = ?",
                                  (LOST, now, t["id"], RUNNING)).rowcount
            _db.commit()
            if claimed == 1:
                t["status"], t["ended"] = LOST, now
                lost.append(t)
        return lost


def _conn() -> sqlite3.Connection:
    if _db is None:
        load()
    return _db


def _select(where: str, params: tuple = (), order: str = "started DESC", limit: int = 0) -> List[dict]:
    sql = f"SELECT id, parent, child, status, started, ended, pid, data FROM tasks WHERE {where} ORDER BY {order}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = []
    for row in _conn().execute(sql, params).fetchall():
        rec = json.loads(row[-1] or "{}")
        rec.update(zip(_COLUMNS, row[:-1]))
        rows.append(rec)
    return rows


def _put(rec: dict) -> None:
    data = {k: v for k, v in rec.items() if k not in _COLUMNS}
    _conn().execute(
        "INSERT OR REPLACE INTO tasks (id, parent, child, status, started, ended, pid, data) VALUES (?,?,?,?,?,?,?,?)",
        (rec["id"], rec["parent"], rec.get("child") or "", rec["status"], rec["started"], rec.get("ended"),
         int(rec.get("pid") or 0), json.dumps(data, ensure_ascii=False)))
    _conn().commit()


def _update(task_id: str, change) -> None:
    with _lock:
        found = _select("id = ?", (task_id,))
        if found and change(found[0]) is not False:
            _put(found[0])


def _expire(db: sqlite3.Connection, now: float) -> None:
    db.execute("DELETE FROM tasks WHERE started < ?", (now - _KEEP_SECONDS,))
    db.execute("DELETE FROM notes WHERE created < ?", (now - _KEEP_SECONDS,))


def _prune(parent: str, now: float) -> None:
    db = _conn()
    _expire(db, now)
    db.execute("DELETE FROM tasks WHERE parent = ? AND status != ? AND id NOT IN (SELECT id FROM tasks "
               "WHERE parent = ? AND status != ? ORDER BY ended DESC LIMIT ?)",
               (parent, RUNNING, parent, RUNNING, _KEEP_FINISHED_PER_PARENT))
    db.commit()


# ---------------------------------------------------------------- writes

def start(task_id: str, parent: str, goal: str, child: str = "") -> None:
    with _lock:
        _put({"id": task_id, "parent": parent, "child": child, "status": RUNNING, "started": time.time(),
              "ended": None, "pid": os.getpid(), "proc": _proc_identity(os.getpid()), "goal": goal, "files": [], "steers": [], "transcript": ""})


def add_files(task_id: str, paths: Iterable[str]) -> None:
    new = [p.strip() for p in paths if isinstance(p, str) and p.strip()]
    if not new:
        return

    def change(t: dict):
        files = t.setdefault("files", [])
        fresh = [p for p in dict.fromkeys(new) if p not in files]
        if not fresh:
            return False
        files.extend(fresh)
        del files[:-_MAX_FILES]
    _update(task_id, change)


def set_transcript(task_id: str, path: str) -> None:
    _update(task_id, lambda t: t.update(transcript=str(path)))


def add_steer(task_id: str, message: str) -> None:
    def change(t: dict):
        steers = t.setdefault("steers", [])
        steers.append(str(message)[:400])
        del steers[:-_MAX_STEERS]
    _update(task_id, change)


def mark_stopped(task_id: str, parent: str) -> None:
    """The main agent stopped this task: it is no longer running, even before its subagent_stop arrives."""
    def change(t: dict):
        if t["parent"] != parent or t["status"] != RUNNING:
            return False
        t.update(status=STOPPED, ended=time.time())
    _update(task_id, change)


def finish(task_id: str, status: str, summary: str = "", evidence: Iterable[str] = (),
           blockers: Iterable[str] = (), files: Iterable[str] = (), unreported: bool = False,
           question: str = "", options: Iterable[str] = ()) -> None:
    """Close a task. *unreported* marks a result the user should hear about; it stays marked until
    a turn of the main agent that saw it ends with a reply nobody interrupted (see reports). A
    *question* marks a task that waits for a decision until the chat dispatches again."""
    add_files(task_id, files)
    now = time.time()
    with _lock:
        found = _select("id = ?", (task_id,))
        if not found:
            return
        t = found[0]
        status = (status or "").strip().lower()
        t.update(status=status if status and status != RUNNING else "unknown", ended=now, summary=str(summary or "")[:600],
                 evidence=[str(e)[:200] for e in evidence][:5], blockers=[str(b)[:200] for b in blockers][:5],
                 unreported=bool(unreported), shown=0, shown_turn="", wakes=0,
                 question=str(question or "")[:600], options=[str(o)[:200] for o in options][:6],
                 awaiting=bool(str(question or "").strip()), asked_turn="")
        _put(t)
        _prune(t["parent"], now)


def mark_background(task_id: str) -> None:
    """The task was dispatched in the background, so its result reaches the main agent in a later turn
    of its own instead of as the delegate_task tool result."""
    def change(t: dict):
        if t.get("background"):
            return False
        t["background"] = True
        if t["status"] not in (RUNNING, STOPPED, LOST):
            t.update(unreported=True, shown=0, shown_turn="", wakes=0)  # it ended before the dispatch returned
    _update(task_id, change)


def mark_unreported(task_id: str) -> None:
    _update(task_id, lambda t: t.update(unreported=True, shown=0, shown_turn="", wakes=0))


def show_unreported(parent: str, turn: str) -> List[dict]:
    """The tasks of *parent* whose result the user has not heard yet, marked as seen by the main agent
    in *turn*. Each returned record carries ``seen_before``: whether an earlier turn already saw it."""
    out = []
    with _lock:
        for t in _unreported(parent):
            seen_before = bool(t.get("shown")) and t.get("shown_turn") != turn
            if t.get("shown_turn") != turn:
                t.update(shown=int(t.get("shown") or 0) + 1, shown_turn=turn)
                _put(t)
            out.append(dict(t, seen_before=seen_before))
    return out


def unreported_in_turn(parent: str, turn: str) -> List[dict]:
    with _lock:
        return [t for t in _unreported(parent) if t.get("shown_turn") == turn]


def settle(task_ids: Iterable[str], turn: str) -> None:
    """The user has heard these results, in the reply of *turn*."""
    def change(t: dict):
        t["unreported"] = False
        if t.get("awaiting") and not t.get("asked_turn"):
            t["asked_turn"] = turn
    for task_id in task_ids:
        _update(task_id, change)


def mark_asked(task_ids: Iterable[str], turn: str) -> None:
    """The question of these tasks reached the main agent in *turn*."""
    for task_id in task_ids:
        _update(task_id, lambda t: t.update(asked_turn=turn))


def stop_awaiting(task_ids: Iterable[str]) -> None:
    for task_id in task_ids:
        _update(task_id, lambda t: t.update(awaiting=False))


def add_wake(task_ids: Iterable[str]) -> None:
    for task_id in task_ids:
        _update(task_id, lambda t: t.update(wakes=int(t.get("wakes") or 0) + 1))


def add_note(parent: str, text: str) -> None:
    """Queue a note for the main agent of *parent*; it survives restarts until that chat's next turn."""
    if not parent or not text:
        return
    with _lock:
        _conn().execute("INSERT INTO notes (parent, created, text) VALUES (?, ?, ?)", (parent, time.time(), text))
        _conn().commit()


def pop_notes(parent: str) -> List[str]:
    if not parent:
        return []
    with _lock:
        db = _conn()
        db.execute("BEGIN IMMEDIATE")
        try:
            rows = db.execute("SELECT id, text FROM notes WHERE parent = ? ORDER BY id", (parent,)).fetchall()
            if rows:
                db.execute("DELETE FROM notes WHERE parent = ? AND id <= ?", (parent, rows[-1][0]))
            db.commit()
        except Exception:
            db.rollback()
            raise
    return [text for _, text in rows]


# ---------------------------------------------------------------- reads

def get(task_id: str) -> Optional[dict]:
    with _lock:
        found = _select("id = ?", (task_id,))
    return found[0] if found else None


def by_child(child_session: str) -> Optional[dict]:
    if not child_session:
        return None
    with _lock:
        found = _select("child = ?", (child_session,), limit=1)
    return found[0] if found else None


def running(parent: str) -> List[dict]:
    with _lock:
        return _select("parent = ? AND status = ? AND started >= ?",
                       (parent, RUNNING, time.time() - _RUNNING_HORIZON_S), order="started ASC")


def _recent(parent: str) -> List[dict]:
    return _select("parent = ? AND status != ? AND ended >= ?", (parent, RUNNING, time.time() - _REPORT_HORIZON_S),
                   order="ended ASC")


def _unreported(parent: str) -> List[dict]:
    return [t for t in _recent(parent) if t.get("unreported")]


def awaiting_decision(parent: str) -> List[dict]:
    """Tasks of *parent* that ended with a question and no dispatch since."""
    with _lock:
        return [t for t in _recent(parent) if t.get("awaiting")]


def unfinished(parent: str, within_seconds: float = 86400.0, limit: int = 3) -> List[dict]:
    """Recent tasks of this chat that ended without completing: the work a follow-up most likely continues."""
    with _lock:
        return _select("parent = ? AND status NOT IN (?, ?) AND ended >= ?",
                       (parent, RUNNING, "completed", time.time() - within_seconds), order="ended DESC", limit=limit)


def minutes_since(ts: Any) -> int:
    try:
        return max(0, int((time.time() - float(ts)) / 60))
    except (TypeError, ValueError):
        return 0


def ended_since(parent: str, since: float, statuses: Iterable[str]) -> List[dict]:
    """Tasks of *parent* that ended with one of *statuses* at or after *since*, oldest first."""
    wanted = tuple(statuses)
    if not parent or not wanted:
        return []
    marks = ",".join("?" * len(wanted))
    with _lock:
        return _select(f"parent = ? AND ended >= ? AND status IN ({marks})", (parent, float(since), *wanted),
                       order="ended ASC")


def latest(limit: int = 10) -> List[dict]:
    """The most recent tasks of every chat: running ones first, then by when they ended."""
    with _lock:
        return _select("1 = 1", order=f"status = '{RUNNING}' DESC, COALESCE(ended, started) DESC", limit=limit)


def location() -> str:
    """The ledger's file, or "" when it is kept in memory."""
    with _lock:
        try:
            for row in _conn().execute("PRAGMA database_list").fetchall():
                if row[1] == "main":
                    return str(row[2] or "")
        except Exception:
            pass
    return ""
