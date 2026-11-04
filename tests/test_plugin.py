"""
Tests for proactive-agent governor plugin (v0.1.3).
Validates positive platform allowlist (governed_platforms: default ["weixin"]),
autonomous subagent and cron bypass, 30s terminal detach, and circuit breaker.
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
    assert data["version"] == "0.1.3"
    assert "description" in data and len(data["description"]) > 10
    assert "hooks" in data
    assert "pre_tool_call" in data["hooks"]
    assert "config_schema" in data
    assert data["config_schema"]["governed_platforms"]["type"] == "list"
    assert data["config_schema"]["governed_platforms"]["default"] == ["weixin"]
    assert "description" in data["config_schema"]["governed_platforms"]
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


def test_system_prompt_rendering_allowlist():
    """Verify system prompt is only rendered for governed platforms (weixin by default)."""
    mock_ctx = MockPluginContext()
    plugin.register(mock_ctx)

    renderer = mock_ctx.prompt_sections["proactive-agent.discipline"]["renderer"]

    # Governed platform ("weixin") gets prompt discipline
    content = renderer({"platform": "weixin"})
    assert "Role: Main dialogue orchestrator" in content
    assert "finish in <=5 tool rounds" in content
    assert ">30s" in content
    assert ">90s" in content
    assert "round 6" in content
    assert "delegate_task" in content
    assert "process_manage" not in content
    assert "Scope minimally and aim to dispatch as early as possible" in content
    assert "before tool round 5 or 90s" in content

    # Non-governed platforms are completely exempt (0 intrusion)
    assert renderer({"platform": "cli"}) == ""
    assert renderer({"platform": "desktop"}) == ""
    assert renderer({"platform": "telegram"}) == ""
    assert renderer({"platform": "slack"}) == ""
    assert renderer(None) == ""
    assert renderer({}) == ""

    # Subagent and cron are completely exempt
    assert renderer({"platform": "subagent"}) == ""
    assert renderer({"platform": "cron"}) == ""
    assert renderer({"platform": "weixin", "session_id": "cron_12345"}) == ""


def test_circuit_breaker_enforcement_weixin():
    """Verify circuit breaker trips on governed platform (weixin) at round 6 / 90s."""
    turn_id = "test_turn_123"
    session_id = "test_sess_123"
    platform = "weixin"

    plugin._on_session_clean(session_id=session_id)
    plugin._on_pre_llm_call(turn_id=turn_id, session_id=session_id, task_id="main", platform=platform)

    # Round 1..5: heavy tool allowed
    for r in range(1, 6):
        plugin._on_pre_api_request(turn_id=turn_id, session_id=session_id, task_id="main", api_request_id=f"req_{r}", platform=platform)
        res = plugin._pre_tool_call("terminal", {"command": "ls"}, turn_id=turn_id, session_id=session_id, task_id="main")
        assert res is None, f"Round {r} should not trip the circuit breaker"

    # Round 6: heavy tool blocked
    plugin._on_pre_api_request(turn_id=turn_id, session_id=session_id, task_id="main", api_request_id="req_6", platform=platform)
    res = plugin._pre_tool_call("terminal", {"command": "ls"}, turn_id=turn_id, session_id=session_id, task_id="main")
    assert res is not None
    assert res.get("action") == "block"
    assert "[Circuit Breaker]" in res.get("reason", "")
    assert ">90s" in res.get("reason", "")

    # Allowed tools should pass even when tripped
    for allowed_tool in ["delegate_task", "skill_manage", "memory", "skill_view", "skills_list"]:
        res = plugin._pre_tool_call(allowed_tool, {}, turn_id=turn_id, session_id=session_id, task_id="main")
        assert res is None, f"Allowed tool {allowed_tool} should pass when circuit breaker is tripped"

    # process_manage must be blocked when tripped
    res = plugin._pre_tool_call("process_manage", {}, turn_id=turn_id, session_id=session_id, task_id="main")
    assert res is not None
    assert res.get("action") == "block"

    # Subagent bypass even on weixin
    res = plugin._pre_tool_call("terminal", {"command": "ls"}, turn_id=turn_id, session_id=session_id, task_id="sa-subtask-1")
    assert res is None, "Subagents starting with 'sa-' must bypass the circuit breaker"
    plugin._on_session_clean(session_id=session_id)


def test_circuit_breaker_exempt_for_cli_and_desktop():
    """Verify non-governed platforms (cli, desktop) are completely exempt from circuit breaker."""
    for exempt_platform in ["cli", "desktop"]:
        turn_id = f"test_turn_{exempt_platform}"
        session_id = f"test_sess_{exempt_platform}"

        plugin._on_session_clean(session_id=session_id)
        plugin._on_pre_llm_call(turn_id=turn_id, session_id=session_id, task_id="main", platform=exempt_platform)

        # Run 10 rounds: must never trip circuit breaker on cli / desktop
        for r in range(1, 11):
            plugin._on_pre_api_request(turn_id=turn_id, session_id=session_id, task_id="main", api_request_id=f"req_{r}", platform=exempt_platform)
            res = plugin._pre_tool_call("terminal", {"command": "cargo build"}, turn_id=turn_id, session_id=session_id, task_id="main")
            assert res is None, f"Platform {exempt_platform} at round {r} must NOT be blocked by circuit breaker"

        plugin._on_session_clean(session_id=session_id)


def test_adaptive_wait_platform_governance():
    """Verify 30s auto-detach timer is applied ONLY on governed platforms (weixin)."""
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

        # 1. On "cli" -> timer NOT scheduled (exempt)
        plugin._on_session_clean()
        plugin._record_platform_context("cli", tid=1001)
        try:
            with patch("threading.Timer") as mock_timer:
                env = MockBaseEnv()
                proc = MagicMock()
                res = env._wait_for_process(proc, timeout=120, watch_interrupt_tid=1001, yield_handler=MagicMock())
                assert res == {"returncode": 0, "stdout": "done"}
                mock_timer.assert_not_called()
        finally:
            plugin._on_session_clean()

        # 2. On "weixin" -> timer IS scheduled
        plugin._record_platform_context("weixin", tid=1002)
        try:
            with patch("threading.Timer") as mock_timer:
                env = MockBaseEnv()
                proc = MagicMock()
                res = env._wait_for_process(proc, timeout=120, watch_interrupt_tid=1002, yield_handler=MagicMock())
                assert res == {"returncode": 0, "stdout": "done"}
                mock_timer.assert_called_once()
                # Default timeout: 30s
                assert mock_timer.call_args[0][0] == 30.0
        finally:
            plugin._on_session_clean()


def test_adaptive_wait_subagent_bypass():
    """Verify subagent completely bypasses 30s auto-detach timer even on weixin platform."""
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

        plugin._on_session_clean()
        plugin._record_platform_context("weixin", tid=12345)
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
    """Verify transform_tool_result transforms for governed platform and stays quiet for subagents/exempt platforms."""
    plugin._on_session_clean()
    tid = threading.get_ident()
    raw_result = json.dumps({"status": "yielded_to_background", "session_id": "test_sess_abc"})

    # 1. On weixin (governed) -> transformed
    plugin._record_platform_context("weixin", tid=tid)
    with plugin._timeout_yield_lock:
        plugin._timeout_yield_tids.add(tid)
    transformed = plugin._transform_tool_result("terminal", {}, raw_result, task_id="main", platform="weixin")
    assert transformed is not None
    data = json.loads(transformed)
    note = data.get("note", "")
    assert "(mention the session_id)" not in note
    assert "write a concise text response informing the user that the command is running in the background, and release the conversation!" in note

    # 2. Subagent on weixin -> returns None
    with plugin._timeout_yield_lock:
        plugin._timeout_yield_tids.add(tid)
    subagent_transformed = plugin._transform_tool_result("terminal", {}, raw_result, task_id="sa-child-99", platform="weixin")
    assert subagent_transformed is None

    # 3. CLI (exempt platform) -> returns None
    plugin._on_session_clean()
    plugin._record_platform_context("cli", tid=tid)
    with plugin._timeout_yield_lock:
        plugin._timeout_yield_tids.add(tid)
    cli_transformed = plugin._transform_tool_result("terminal", {}, raw_result, task_id="main", platform="cli")
    assert cli_transformed is None
    plugin._on_session_clean()


def test_human_intervention_steer_reset():
    """Verify steer message resets turn quota on weixin platform."""
    turn_id = "test_turn_steer"
    session_id = "test_sess_steer"
    platform = "weixin"

    plugin._on_session_clean(session_id=session_id)
    plugin._on_pre_llm_call(turn_id=turn_id, session_id=session_id, task_id="main", platform=platform)

    # Advance to round 6 to trip breaker
    for r in range(1, 7):
        plugin._on_pre_api_request(turn_id=turn_id, session_id=session_id, task_id="main", api_request_id=f"r_{r}", platform=platform)

    # Check tripped
    res = plugin._pre_tool_call("terminal", {"command": "ls"}, turn_id=turn_id, session_id=session_id, task_id="main")
    assert res is not None

    # User sends steer message mid-turn!
    messages = [
        {"role": "user", "content": "Initial task"},
        {"role": "assistant", "content": "Working on it"},
        {"role": "user", "content": "[OUT-OF-BAND USER MESSAGE] Please cancel that and do this [/OUT-OF-BAND USER MESSAGE]"}
    ]
    plugin._on_pre_api_request(turn_id=turn_id, session_id=session_id, task_id="main", api_request_id="r_steer", request_messages=messages, platform=platform)

    # Circuit breaker should now be reset!
    res = plugin._pre_tool_call("terminal", {"command": "ls"}, turn_id=turn_id, session_id=session_id, task_id="main")
    assert res is None, "Human intervention must reset turn quota"
    plugin._on_session_clean(session_id=session_id)


def test_cron_zero_prompt_injection():
    """Verify cron sessions receive zero prompt injection (completely empty discipline section)."""
    mock_ctx = MockPluginContext()
    plugin.register(mock_ctx)

    renderer = mock_ctx.prompt_sections["proactive-agent.discipline"]["renderer"]

    # Cron sessions by platform
    assert renderer({"platform": "cron"}) == ""

    # Cron sessions by session_id prefix / pattern
    assert renderer({"platform": "weixin", "session_id": "cron_138c1eb86f83_20261104_093000"}) == ""
    assert renderer({"platform": "weixin", "session_id": "cron:138c1eb86f83:79ea4521634d4b85a11f3a1debdb45c5"}) == ""
    assert renderer({"platform": "weixin", "session_id": "local-abc:cron:138c1eb86f83:79ea4521"}) == ""

    # Cron context via ContextVar
    plugin._record_cron_context(True)
    try:
        assert renderer(None) == ""
        assert renderer({"platform": "weixin"}) == ""
    finally:
        plugin._on_session_clean()

    # Governed platform weixin gets discipline prompt
    assert renderer({"platform": "weixin", "session_id": "sess_123"}) != ""


def test_cron_circuit_breaker_bypass():
    """Verify Cron tasks are completely exempt from the circuit breaker (>5 rounds or >90s) even on weixin."""
    # 1. Bypass by task_id ("cron:...")
    turn_id = "turn_cron_1"
    session_id = "local-cron-session-1"
    platform = "weixin"
    plugin._on_session_clean(session_id=session_id)

    task_id_cron = "cron:138c1eb86f83:79ea4521634d4b85a11f3a1debdb45c5"
    plugin._on_pre_llm_call(turn_id=turn_id, session_id=session_id, task_id=task_id_cron, platform=platform)

    for r in range(1, 10):
        plugin._on_pre_api_request(turn_id=turn_id, session_id=session_id, task_id=task_id_cron, api_request_id=f"r_{r}", platform=platform)
        res = plugin._pre_tool_call("terminal", {"command": f"step {r}"}, turn_id=turn_id, session_id=session_id, task_id=task_id_cron)
        assert res is None, f"Cron task at round {r} must NOT be blocked by circuit breaker"
        plugin._post_tool_call("terminal", {"command": f"step {r}"}, '{"output": "ok"}', turn_id=turn_id, session_id=session_id, task_id=task_id_cron, duration_ms=25000)

    plugin._on_session_clean(session_id=session_id)

    # 2. Bypass by session_id ("cron_...")
    turn_id_2 = "turn_cron_2"
    session_id_2 = "cron_138c1eb86f83_20261104_093000"
    plugin._on_pre_llm_call(turn_id=turn_id_2, session_id=session_id_2, task_id="main", platform=platform)
    for r in range(1, 8):
        plugin._on_pre_api_request(turn_id=turn_id_2, session_id=session_id_2, task_id="main", api_request_id=f"r2_{r}", platform=platform)
        res = plugin._pre_tool_call("read_file", {"path": f"file_{r}.txt"}, turn_id=turn_id_2, session_id=session_id_2, task_id="main")
        assert res is None, f"Cron session_id at round {r} must NOT be blocked"

    plugin._on_session_clean(session_id=session_id_2)

    # 3. Bypass by platform="cron"
    turn_id_3 = "turn_cron_3"
    session_id_3 = "custom_sess_3"
    plugin._on_pre_llm_call(turn_id=turn_id_3, session_id=session_id_3, task_id="main", platform="cron")
    for r in range(1, 8):
        plugin._on_pre_api_request(turn_id=turn_id_3, session_id=session_id_3, task_id="main", api_request_id=f"r3_{r}", platform="cron")
        res = plugin._pre_tool_call("write_file", {"path": "test.txt", "content": "hi"}, turn_id=turn_id_3, session_id=session_id_3, task_id="main", platform="cron")
        assert res is None, f"Cron platform at round {r} must NOT be blocked"

    plugin._on_session_clean(session_id=session_id_3)


def test_cron_adaptive_wait_bypass():
    """Verify Cron tasks bypass 30s auto-detach timer in adaptive wait."""
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

        # Set cron context via task_id on weixin
        plugin._record_platform_context("weixin", tid=12345)
        plugin._current_task_id.set("cron:138c1eb86f83:execution_42")
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


def test_cron_transform_tool_result_bypass():
    """Verify Cron tasks do not have terminal output rewritten."""
    plugin._on_session_clean()
    tid = threading.get_ident()
    with plugin._timeout_yield_lock:
        plugin._timeout_yield_tids.add(tid)

    raw_result = json.dumps({"status": "yielded_to_background", "session_id": "cron_sess_abc"})

    # Cron task_id should return None (not rewritten)
    cron_transformed = plugin._transform_tool_result(
        "terminal", {}, raw_result, task_id="cron:138c1eb86f83:exec_99", platform="weixin"
    )
    assert cron_transformed is None

    # Cron session_id should return None
    with plugin._timeout_yield_lock:
        plugin._timeout_yield_tids.add(tid)
    cron_transformed_sid = plugin._transform_tool_result(
        "terminal", {}, raw_result, session_id="cron_138c1eb86f83_20261104", platform="weixin"
    )
    assert cron_transformed_sid is None
    plugin._on_session_clean()


def test_cron_detection_runtime_hooks():
    """Verify _is_cron_context accurately catches core Hermes cron signals."""
    plugin._on_session_clean()

    # 1. Hermes current_cron_execution
    mock_identity = MagicMock()
    mock_identity.current_cron_execution.return_value = MagicMock()
    with patch.dict(sys.modules, {"cron": MagicMock(), "cron.execution_identity": mock_identity}):
        assert plugin._is_cron_context() is True
    plugin._on_session_clean()

    # 2. Gateway current_execution
    mock_session_cron = MagicMock()
    mock_session_cron.current_execution.return_value = ("auth", "sid", "jid", "eid")
    with patch.dict(sys.modules, {"gateway": MagicMock(), "gateway.session_cron": mock_session_cron}):
        assert plugin._is_cron_context() is True
    plugin._on_session_clean()

    # 3. HERMES_CRON_SESSION env in gateway session context
    mock_session_context = MagicMock()
    mock_session_context.get_session_env.return_value = "1"
    with patch.dict(sys.modules, {"gateway": MagicMock(), "gateway.session_context": mock_session_context}):
        assert plugin._is_cron_context() is True
    plugin._on_session_clean()

    # 4. OS environment variable
    with patch.dict(os.environ, {"HERMES_CRON_SESSION": "1"}):
        assert plugin._is_cron_context() is True
    plugin._on_session_clean()

    with patch.dict(os.environ, {"_HERMES_CRON_EXTERNAL_WORKER": "exec_abc"}):
        assert plugin._is_cron_context() is True
    plugin._on_session_clean()


def test_governed_platforms_custom_plugin_config():
    """Verify governed_platforms can be customized via plugin context configuration."""
    mock_ctx = MockPluginContext({"governed_platforms": ["desktop", "telegram"]})
    plugin.register(mock_ctx)

    renderer = mock_ctx.prompt_sections["proactive-agent.discipline"]["renderer"]

    # Desktop and telegram are now governed!
    assert "Role: Main dialogue orchestrator" in renderer({"platform": "desktop"})
    assert "Role: Main dialogue orchestrator" in renderer({"platform": "telegram"})

    # Weixin and cli are now exempt!
    assert renderer({"platform": "weixin"}) == ""
    assert renderer({"platform": "cli"}) == ""

    # Test circuit breaker follows custom configuration
    turn_id = "test_custom_desktop"
    session_id = "sess_custom_desktop"
    plugin._on_session_clean(session_id=session_id)
    plugin._on_pre_llm_call(turn_id=turn_id, session_id=session_id, task_id="main", platform="desktop")

    for r in range(1, 7):
        plugin._on_pre_api_request(turn_id=turn_id, session_id=session_id, task_id="main", api_request_id=f"r_{r}", platform="desktop")

    res = plugin._pre_tool_call("terminal", {"command": "ls"}, turn_id=turn_id, session_id=session_id, task_id="main")
    assert res is not None
    assert res.get("action") == "block"
    plugin._on_session_clean(session_id=session_id)


def test_governed_platforms_env_var():
    """Verify PROACTIVE_GOVERNED_PLATFORMS environment variable overrides defaults."""
    # 1. Comma-separated string
    with patch.dict(os.environ, {"PROACTIVE_GOVERNED_PLATFORMS": "cli,slack"}):
        mock_ctx = MockPluginContext()
        plugin.register(mock_ctx)
        renderer = mock_ctx.prompt_sections["proactive-agent.discipline"]["renderer"]

        assert "Role: Main dialogue orchestrator" in renderer({"platform": "cli"})
        assert "Role: Main dialogue orchestrator" in renderer({"platform": "slack"})
        assert renderer({"platform": "weixin"}) == ""
        assert renderer({"platform": "desktop"}) == ""

    # 2. JSON list format
    with patch.dict(os.environ, {"PROACTIVE_GOVERNED_PLATFORMS": '["discord", "telegram"]'}):
        mock_ctx = MockPluginContext()
        plugin.register(mock_ctx)
        renderer = mock_ctx.prompt_sections["proactive-agent.discipline"]["renderer"]

        assert "Role: Main dialogue orchestrator" in renderer({"platform": "discord"})
        assert "Role: Main dialogue orchestrator" in renderer({"platform": "telegram"})
        assert renderer({"platform": "weixin"}) == ""
        assert renderer({"platform": "cli"}) == ""


def test_governed_platforms_empty_disables_governance():
    """Verify setting governed_platforms to empty list disables all governance everywhere."""
    mock_ctx = MockPluginContext({"governed_platforms": []})
    plugin.register(mock_ctx)
    renderer = mock_ctx.prompt_sections["proactive-agent.discipline"]["renderer"]

    assert renderer({"platform": "weixin"}) == ""
    assert renderer({"platform": "cli"}) == ""
    assert renderer({"platform": "desktop"}) == ""

    # Circuit breaker never trips even after 10 rounds on weixin
    turn_id = "test_empty_allowlist"
    session_id = "sess_empty_allowlist"
    plugin._on_session_clean(session_id=session_id)
    plugin._on_pre_llm_call(turn_id=turn_id, session_id=session_id, task_id="main", platform="weixin")
    for r in range(1, 11):
        plugin._on_pre_api_request(turn_id=turn_id, session_id=session_id, task_id="main", api_request_id=f"r_{r}", platform="weixin")
        res = plugin._pre_tool_call("terminal", {"command": "echo hi"}, turn_id=turn_id, session_id=session_id, task_id="main")
        assert res is None, "Empty allowlist must disable circuit breaker"
    plugin._on_session_clean(session_id=session_id)
