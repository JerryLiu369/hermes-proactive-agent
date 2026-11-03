"""
Tests for proactive-agent governor plugin (v0.1.1).
"""

import json
import os
import sys
import threading
import yaml
import pytest
from unittest.mock import MagicMock, patch

# Ensure repo root is in python path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import __init__ as plugin


class MockPluginContext:
    def __init__(self, config=None):
        self.config = config or {}
        self.hooks = {}
        self.prompt_sections = {}

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def register_hook(self, hook_name, fn):
        self.hooks[hook_name] = fn

    def register_system_prompt_section(self, name, renderer, position="after_memory", max_chars=2000):
        self.prompt_sections[name] = {
            "renderer": renderer,
            "position": position,
            "max_chars": max_chars,
        }


def test_manifest_validity():
    manifest_path = os.path.join(REPO_ROOT, "plugin.yaml")
    assert os.path.exists(manifest_path)
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data["name"] == "proactive-agent"
    assert data["version"] == "0.1.1"
    assert "description" in data and len(data["description"]) > 10
    assert "hooks" in data
    assert "pre_tool_call" in data["hooks"]
    assert "config_schema" in data
    assert data["config_schema"]["terminal_auto_detach_seconds"]["default"] == 30
    assert data["config_schema"]["turn_max_tool_rounds"]["default"] == 5
    assert data["config_schema"]["turn_max_duration_seconds"]["default"] == 90


def test_plugin_registration():
    mock_ctx = MockPluginContext()
    plugin.register(mock_ctx)

    expected_hooks = [
        "pre_tool_call",
        "post_tool_call",
        "transform_tool_result",
        "pre_llm_call",
        "pre_api_request",
        "agent_loop_stopped",
        "on_session_reset",
        "on_session_end",
    ]
    for hook in expected_hooks:
        assert hook in mock_ctx.hooks, f"Hook {hook} was not registered"

    assert "proactive-agent.discipline" in mock_ctx.prompt_sections


def test_system_prompt_rendering():
    mock_ctx = MockPluginContext()
    plugin.register(mock_ctx)

    renderer = mock_ctx.prompt_sections["proactive-agent.discipline"]["renderer"]
    content = renderer()
    assert "Role: Main dialogue orchestrator" in content
    assert "finish in <=5 tool rounds" in content
    assert ">30s" in content
    assert ">90s" in content
    assert "round 6" in content
    assert "process_manage" in content


def test_subagent_zero_prompt_injection():
    mock_ctx = MockPluginContext()
    plugin.register(mock_ctx)

    renderer = mock_ctx.prompt_sections["proactive-agent.discipline"]["renderer"]
    # Subagent gets completely empty string (zero injection)
    assert renderer({"platform": "subagent"}) == ""
    # Other platforms get prompt
    assert renderer({"platform": "desktop"}) != ""
    assert renderer({"platform": "cli"}) != ""
    assert renderer(None) != ""


def test_circuit_breaker_enforcement():
    turn_id = "test_turn_123"
    session_id = "test_sess_123"

    # Reset turn state
    plugin._on_session_clean(session_id=session_id)
    plugin._on_pre_llm_call(turn_id=turn_id, session_id=session_id, task_id="main")

    # Round 1..5: heavy tool allowed
    for r in range(1, 6):
        plugin._on_pre_api_request(turn_id=turn_id, session_id=session_id, task_id="main", api_request_id=f"req_{r}")
        res = plugin._pre_tool_call("terminal", {"command": "ls"}, turn_id=turn_id, session_id=session_id, task_id="main")
        assert res is None, f"Round {r} should not trip the circuit breaker"

    # Round 6: heavy tool blocked
    plugin._on_pre_api_request(turn_id=turn_id, session_id=session_id, task_id="main", api_request_id="req_6")
    res = plugin._pre_tool_call("terminal", {"command": "ls"}, turn_id=turn_id, session_id=session_id, task_id="main")
    assert res is not None
    assert res.get("action") == "block"
    assert "[Circuit Breaker]" in res.get("reason", "")
    assert ">90s" in res.get("reason", "")

    # Allowed tools should pass even when tripped
    for allowed_tool in ["delegate_task", "process_manage", "skill_manage", "memory", "skill_view", "skills_list"]:
        res = plugin._pre_tool_call(allowed_tool, {}, turn_id=turn_id, session_id=session_id, task_id="main")
        assert res is None, f"Allowed tool {allowed_tool} should pass when circuit breaker is tripped"

    # Subagent bypass
    res = plugin._pre_tool_call("terminal", {"command": "ls"}, turn_id=turn_id, session_id=session_id, task_id="sa-subtask-1")
    assert res is None, "Subagents starting with 'sa-' must bypass the circuit breaker"
    plugin._on_session_clean(session_id=session_id)


def test_adaptive_wait_subagent_bypass():
    """Verify subagent completely bypasses 30s auto-detach timer in adaptive wait."""
    class MockBaseEnv:
        def _wait_for_process(self, proc, timeout=120, **kwargs):
            return {"returncode": 0, "stdout": "done"}

    mock_base_mod = MagicMock()
    mock_base_mod.BaseEnvironment = MockBaseEnv
    mock_interrupt_mod = MagicMock()

    with patch.dict(sys.modules, {
        "tools": MagicMock(),
        "tools.environments": MagicMock(),
        "tools.environments.base": mock_base_mod,
        "tools.interrupt": mock_interrupt_mod,
    }):
        plugin._patched = False
        plugin._install_adaptive_terminal_patch()

        # Set task_id as subagent
        plugin._current_task_id.set("sa-child-42")
        try:
            with patch("threading.Timer") as mock_timer:
                env = MockBaseEnv()
                proc = MagicMock()
                res = env._wait_for_process(
                    proc,
                    timeout=120,
                    watch_interrupt_tid=12345,
                    yield_handler=MagicMock(),
                )
                assert res == {"returncode": 0, "stdout": "done"}
                mock_timer.assert_not_called()
        finally:
            plugin._on_session_clean()


def test_transform_tool_result_quiet():
    """Verify transform_tool_result removes session_id mention and stays quiet."""
    plugin._on_session_clean()
    tid = threading.get_ident()
    with plugin._timeout_yield_lock:
        plugin._timeout_yield_tids.add(tid)

    raw_result = json.dumps({"status": "yielded_to_background", "session_id": "test_sess_abc"})
    transformed = plugin._transform_tool_result("terminal", {}, raw_result, task_id="main")
    assert transformed is not None
    data = json.loads(transformed)
    note = data.get("note", "")
    assert "(mention the session_id)" not in note
    assert "write a concise text response informing the user that the command is running in the background, and release the conversation!" in note

    # Subagents should return None
    with plugin._timeout_yield_lock:
        plugin._timeout_yield_tids.add(tid)
    subagent_transformed = plugin._transform_tool_result("terminal", {}, raw_result, task_id="sa-child-99")
    assert subagent_transformed is None
    plugin._on_session_clean()


def test_human_intervention_steer_reset():
    turn_id = "test_turn_steer"
    session_id = "test_sess_steer"

    plugin._on_session_clean(session_id=session_id)
    plugin._on_pre_llm_call(turn_id=turn_id, session_id=session_id, task_id="main")

    # Advance to round 6 to trip breaker
    for r in range(1, 7):
        plugin._on_pre_api_request(turn_id=turn_id, session_id=session_id, task_id="main", api_request_id=f"r_{r}")

    # Check tripped
    res = plugin._pre_tool_call("terminal", {"command": "ls"}, turn_id=turn_id, session_id=session_id, task_id="main")
    assert res is not None

    # User sends steer message mid-turn!
    messages = [
        {"role": "user", "content": "Initial task"},
        {"role": "assistant", "content": "Working on it"},
        {"role": "user", "content": "[OUT-OF-BAND USER MESSAGE] Please cancel that and do this [/OUT-OF-BAND USER MESSAGE]"}
    ]
    plugin._on_pre_api_request(turn_id=turn_id, session_id=session_id, task_id="main", api_request_id="r_steer", request_messages=messages)

    # Circuit breaker should now be reset!
    res = plugin._pre_tool_call("terminal", {"command": "ls"}, turn_id=turn_id, session_id=session_id, task_id="main")
    assert res is None, "Human intervention must reset turn quota"
    plugin._on_session_clean(session_id=session_id)
