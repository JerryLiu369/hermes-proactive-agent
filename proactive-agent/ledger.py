"""Task ledger: one row per delegated task, shared by the main agent and its workers.

Only the plugin's hooks write it, and no model is called. It lives in Hermes' plugin-data
directory, so a gateway restart keeps what was running, which files each task changed and how
it ended. The main agent reads it through result and restart notes, which wait in the ledger
until that chat's next turn; workers read it through their handoff briefing.
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

logger = logging.getLogger("hermes.plugins.proactive_agent")

PLUGIN_NAME = "proactive-agent"
RUNNING = "running"
LOST = "lost"
STOPPED = "stopped"

_KEEP_SECONDS = 7 * 86400.0
# Same horizon as the in-memory worker registry: a record older than this whose subagent_stop
# never arrived is no longer treated as running.
_RUNNING_HORIZON_S = 6 * 3600.0
_KEEP_FINISHED_PER_PARENT = 20
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
        logger.debug("proactive-agent: no plugin-data directory (%s); task ledger kept in memory", e)
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


def load(path: Optional[Path] = None) -> List[dict]:
    """Open the ledger and return the tasks a Hermes process that has since exited left running.
    Those are marked lost: Hermes does not resume a child across a restart."""
    global _db
    with _lock:
        if _db is not None:
            try:
                _db.close()
            except Exception:
                pass
        _db = sqlite3.connect(str(path), check_same_thread=False) if path else _open_default()
        for statement in _SCHEMA:
            _db.execute(statement)
        _db.commit()
        now = time.time()
        _db.execute("DELETE FROM tasks WHERE started < ?", (now - _KEEP_SECONDS,))
        _db.execute("DELETE FROM notes WHERE created < ?", (now - _KEEP_SECONDS,))
        _db.commit()
        lost = []
        for t in _select("status = ?", (RUNNING,)):
            if t["pid"] == os.getpid() or _alive(int(t["pid"] or 0)):
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


def _prune(parent: str, now: float) -> None:
    db = _conn()
    db.execute("DELETE FROM tasks WHERE started < ?", (now - _KEEP_SECONDS,))
    db.execute("DELETE FROM notes WHERE created < ?", (now - _KEEP_SECONDS,))
    db.execute("DELETE FROM tasks WHERE parent = ? AND status != ? AND id NOT IN (SELECT id FROM tasks "
               "WHERE parent = ? AND status != ? ORDER BY ended DESC LIMIT ?)",
               (parent, RUNNING, parent, RUNNING, _KEEP_FINISHED_PER_PARENT))
    db.commit()


# ---------------------------------------------------------------- writes

def start(task_id: str, parent: str, goal: str, child: str = "") -> None:
    with _lock:
        _put({"id": task_id, "parent": parent, "child": child, "status": RUNNING, "started": time.time(),
              "ended": None, "pid": os.getpid(), "goal": goal, "files": [], "steers": [], "transcript": ""})


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
           blockers: Iterable[str] = (), files: Iterable[str] = ()) -> None:
    add_files(task_id, files)
    now = time.time()
    with _lock:
        found = _select("id = ?", (task_id,))
        if not found:
            return
        t = found[0]
        status = (status or "").strip().lower()
        t.update(status=status if status and status != RUNNING else "unknown", ended=now, summary=str(summary or "")[:600],
                 evidence=[str(e)[:200] for e in evidence][:5], blockers=[str(b)[:200] for b in blockers][:5])
        _put(t)
        _prune(t["parent"], now)


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
