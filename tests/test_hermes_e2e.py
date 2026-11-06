"""End-to-end checks against a real Hermes checkout (skipped when Hermes is not importable).

Loads the plugin through Hermes' own plugin loader from a temporary HERMES_HOME and drives it
through Hermes' hook dispatch, so hook kwargs, the worker-thread/contextvar model, arg rewriting
and config lookup are the real ones.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

import pytest
import yaml

from helpers import PLUGIN_DIR

plugins_mod = pytest.importorskip("hermes_cli.plugins")

SESSION = "e2e-session"
CONTEXT = (
    "Known: https://app.example.com/ping returns 522 since the 10:00 deploy. Target: /srv/app/wrangler.toml. "
    "Do not touch other routes. Stop after one redeploy. Evidence: status line of the public URL."
)


@pytest.fixture
def hermes(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    (home / "plugins").mkdir(parents=True)
    shutil.copytree(PLUGIN_DIR, home / "plugins" / "proactive-agent", ignore=shutil.ignore_patterns("__pycache__"))
    (home / "config.yaml").write_text(yaml.safe_dump({
        "plugins": {
            "enabled": ["proactive-agent"],
            "entries": {"proactive-agent": {"settings": {
                "handoff_min_context_chars": 40,
                "terminal_auto_detach_seconds": 1,
            }}},
        },
    }), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    plugins_mod.discover_plugins(force=True)
    manager = plugins_mod.get_plugin_manager()
    assert manager.has_hook("pre_tool_call"), "plugin did not load through the Hermes loader"
    yield manager


def _pre_tool(name, args, **kw):
    ids = {"session_id": SESSION, "turn_id": "t1", "task_id": "main", "tool_call_id": f"call-{time.time_ns()}"}
    ids.update(kw)
    return plugins_mod._dispatch_pre_tool_call_hooks(name, args, **ids)


def _start_turn(turn="t1"):
    return plugins_mod.invoke_hook(
        "pre_llm_call", session_id=SESSION, task_id="main", turn_id=turn, platform="weixin",
        user_message="帮我修一下 522", conversation_history=[{"role": "user", "content": "帮我修一下 522"}],
        is_first_turn=True, model="m")


def test_main_agent_through_hermes_dispatch(hermes):
    _start_turn()
    block, _ = _pre_tool("process_manage", {"action": "wait"})
    assert block and "blocks the chat" in block
    block, modified = _pre_tool("terminal", {"command": "ls"})
    assert block is None and modified is None


def test_handoff_gate_rewrites_args_through_hermes(hermes):
    _start_turn()
    block, _ = _pre_tool("delegate_task", {"tasks": [{"goal": "修复 522", "context": "看下"}]})
    assert block and "Handoff returned once" in block

    block, modified = _pre_tool("delegate_task", {"tasks": [{"goal": "修复 522", "context": CONTEXT}]})
    assert block is None
    task = modified["tasks"][0]
    assert task["context"].startswith(CONTEXT)
    assert "Handoff briefing" in task["context"] and "帮我修一下 522" in task["context"]
    assert task["output_schema"]["required"] == ["status", "summary"]


def test_single_goal_handoff_keeps_the_goal_through_hermes(hermes):
    _start_turn()
    block, modified = _pre_tool("delegate_task", {"goal": "修复 522", "context": CONTEXT})
    assert block is None
    assert modified["goal"] == "修复 522", "Hermes merges modify args into the original call"
    assert "Handoff briefing" in modified["context"]


def test_worker_limits_through_hermes(hermes):
    _start_turn()
    _, modified = _pre_tool("delegate_task", {"tasks": [{"goal": "修复 522", "context": CONTEXT}]})
    plugins_mod.invoke_hook("subagent_start", parent_session_id=SESSION, parent_turn_id="t1",
                            parent_subagent_id=None, child_session_id="child-e2e",
                            child_subagent_id="sa-0-e2e", child_role="leaf", child_goal="修复 522")
    child = {"task_id": "sa-0-e2e", "session_id": "child-e2e", "turn_id": "ct"}
    curl = {"command": "curl -sI https://app.example.com/ping"}
    for i in range(5):
        plugins_mod.invoke_hook("post_tool_call", tool_name="terminal", args=curl,
                                result='{"output": "HTTP/2 522", "exit_code": 0}', duration_ms=200,
                                status="success", error_type=None, error_message=None,
                                api_request_id=f"r{i}", tool_call_id=f"c{i}", **child)
    block, _ = _pre_tool("terminal", {"command": "ls"}, **child)
    assert block and "STOP" in block

    plugins_mod.invoke_hook("subagent_stop", parent_session_id=SESSION, parent_turn_id="t1",
                            child_session_id="child-e2e", child_role="leaf", child_summary="done",
                            child_status="completed", tool_call_history=[], duration_ms=5000)
    results = _start_turn("t2")
    text = "\n".join(str(r.get("context", "")) for r in results if isinstance(r, dict))
    assert "The plugin stopped it" in text

    db_path = os.path.join(os.environ["HERMES_HOME"], "plugin-data", "proactive-agent", "ledger.db")
    with sqlite3.connect(db_path) as db:
        row = db.execute("SELECT parent, status FROM tasks WHERE id = 'sa-0-e2e'").fetchone()
    assert row == (SESSION, "blocked"), "the ledger lives in Hermes' plugin-data directory"


def test_background_detach_with_real_wait_loop(hermes, monkeypatch):
    from tools.environments.base import BaseEnvironment

    _start_turn()
    monkeypatch.setenv("HERMES_SESSION_ID", SESSION)
    yielded = {}

    def yield_handler(proc, output_so_far):
        yielded["output"] = output_so_far
        return {"yielded_session_id": "proc_e2e", "output": output_so_far, "pid": proc.pid}

    proc = subprocess.Popen([sys.executable, "-c", "import time; print('working', flush=True); time.sleep(8)"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    env_cls = type("_E2EEnv", (BaseEnvironment,), {})
    env_cls.__abstractmethods__ = frozenset()
    env = object.__new__(env_cls)
    started = time.monotonic()
    try:
        result = BaseEnvironment._wait_for_process(env, proc, timeout=60, watch_interrupt_tid=threading.get_ident(),
                                                   yield_handler=yield_handler)
    finally:
        proc.kill()
        proc.wait()
    assert result.get("yielded_session_id") == "proc_e2e"
    assert time.monotonic() - started < 6, "yield should fire after ~1s, not wait for the process"


def test_tool_names_the_plugin_relies_on_exist_in_hermes(hermes):
    import model_tools  # noqa: F401  (registers Hermes' built-in tools)
    from tools.registry import registry

    plugin = next(m for name, m in sys.modules.items()
                  if name.endswith("proactive-agent.secretary") or name.endswith("proactive_agent.secretary"))
    names = set(plugin.RED_ALLOWED_TOOLS) | {"terminal", "delegate_task", "process_manage", "write_file", "patch"}
    missing = sorted(n for n in names if registry.get_entry(n) is None)
    assert not missing, f"tools renamed or removed in Hermes: {missing}"


def test_report_cut_short_by_the_user_reaches_the_next_turn_through_hermes(hermes):
    import inspect
    assert "session_key" in inspect.signature(plugins_mod.PluginContext.inject_message).parameters

    _start_turn()
    plugins_mod.invoke_hook("subagent_start", parent_session_id=SESSION, parent_turn_id="t1",
                            parent_subagent_id=None, child_session_id="child-r", child_subagent_id="sa-0-r",
                            child_role="leaf", child_goal="清理磁盘")
    plugins_mod.invoke_hook("post_tool_call", tool_name="delegate_task", args={}, task_id="main", session_id=SESSION,
                            turn_id="t1", tool_call_id="c-d", api_request_id="r-d", duration_ms=50, status="ok",
                            error_type=None, error_message=None,
                            result='{"status": "dispatched", "mode": "background", "subagent_ids": ["sa-0-r"]}')
    plugins_mod.invoke_hook("on_session_end", session_id=SESSION, task_id="main", turn_id="t1", completed=True,
                            failed=False, interrupted=False, turn_exit_reason="text_response(finish_reason=stop)",
                            model="m", platform="weixin")
    plugins_mod.invoke_hook("subagent_stop", parent_session_id=SESSION, parent_turn_id="t1",
                            child_session_id="child-r", child_role="leaf", child_status="completed",
                            child_summary='{"status": "completed", "summary": "freed 3.4 GiB", "evidence": ["df -h"]}',
                            tool_call_history=[], duration_ms=5000)
    _start_turn("t2")
    redirect = {"role": "user", "content": "返回机制呢？", "api_content": (
        "[Context from the interrupted assistant response]\n[This response was interrupted by a user correction.]"
        "\n\n返回机制呢？")}
    plugins_mod.invoke_hook("pre_api_request", session_id=SESSION, turn_id="t2", task_id="main", platform="weixin",
                            conversation_history=[redirect], request_messages=[])
    plugins_mod.invoke_hook("on_session_end", session_id=SESSION, task_id="main", turn_id="t2", completed=True,
                            failed=False, interrupted=False, turn_exit_reason="text_response(finish_reason=stop)",
                            model="m", platform="weixin")
    results = _start_turn("t3")
    text = "\n".join(str(r.get("context", "")) for r in results if isinstance(r, dict))
    assert "has not heard" in text and "freed 3.4 GiB" in text
