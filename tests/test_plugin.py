"""Behaviour tests for proactive-agent (secretary governance, handoff contract, worker guards)."""

import json
import os
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml

from conftest import REPO_ROOT

GOOD_CONTEXT = (
    "Known: https://app.example.com/ping returns 522 since the deploy at 10:00; DNS record is proxied. "
    "Target: /srv/app/wrangler.toml and the worker route for app.example.com. Do not touch other routes. "
    "Stop after one redeploy if 522 persists. Evidence: curl -sI status line of the public URL."
)
CHILD = dict(task_id="sa-0-abc", session_id="child-1", turn_id="ct")


def start_turn(plugin, session="s1", turn="t1", platform="weixin", history=None, user="please fix the site"):
    return plugin._pre_llm_call(session_id=session, turn_id=turn, platform=platform,
                                conversation_history=history or [], user_message=user)


def main_tool(plugin, name, args=None, session="s1", turn="t1"):
    return plugin._pre_tool_call(name, args or {}, session_id=session, turn_id=turn, task_id="main")


def main_post(plugin, name, args, result, session="s1", turn="t1"):
    plugin._post_tool_call(name, args, result, session_id=session, turn_id=turn, task_id="main")


def spawn_worker(plugin, goal="Fix the 522 on app.example.com", context=GOOD_CONTEXT, sub_id="sa-0-abc",
                 child_session="child-1"):
    res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": goal, "context": context}]})
    assert res and res["action"] == "modify", res
    plugin._subagent_start(parent_session_id="s1", child_session_id=child_session, child_subagent_id=sub_id,
                           child_goal=goal)
    worker = plugin.workers.lookup(task_id=sub_id)
    assert worker is not None
    return worker


def w_tool(plugin, name, args, api="r1"):
    return plugin._pre_tool_call(name, args, api_request_id=api, **CHILD)


def w_post(plugin, name, args, result, api="r1"):
    plugin._post_tool_call(name, args, result, api_request_id=api, **CHILD)


# ---------------------------------------------------------------- manifest & registration

def test_manifest_matches_code(plugin):
    with open(os.path.join(REPO_ROOT, "plugin.yaml"), encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    assert manifest["version"] == plugin.__version__ == "0.1.9"
    for hook in ("pre_tool_call", "post_tool_call", "transform_tool_result", "pre_llm_call", "pre_api_request",
                 "subagent_start", "subagent_stop", "on_session_end", "on_session_reset", "on_session_finalize"):
        assert hook in manifest["hooks"]
    schema = manifest["config_schema"]
    for key, default in plugin.config.DEFAULTS.items():
        assert key in schema, key
        assert schema[key]["default"] == default, key
    assert schema["turn_soft_seconds"]["default"] == 60.0
    assert schema["turn_hard_seconds"]["default"] == 120.0


def test_register_wires_hooks_prompt_and_aux_task(plugin, make_ctx):
    ctx = make_ctx()
    assert set(ctx.hooks) >= {"pre_tool_call", "subagent_start", "subagent_stop", "pre_llm_call"}
    assert "proactive-agent.discipline" in ctx.prompt_sections
    assert plugin.handoff.AUX_TASK in ctx.aux_tasks


def test_prompt_only_for_governed_main(plugin, make_ctx):
    ctx = make_ctx()
    render = ctx.prompt_sections["proactive-agent.discipline"]["renderer"]
    text = render({"platform": "weixin"})
    assert "~60s" in text and "At 120s" in text and "30s" in text
    for info in ({"platform": "cli"}, {"platform": "subagent"}, {"platform": "cron"},
                 {"platform": "weixin", "session_id": "cron_1"}, None, {}):
        assert render(info) == ""


# ---------------------------------------------------------------- secretary turn budget

def test_turn_budget_yellow_then_red(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    assert main_tool(plugin, "terminal", {"command": "ls"}) is None

    plugin.secretary._turns["s1"]["start"] -= 65
    assert main_tool(plugin, "terminal", {"command": "ls"}) is None, "yellow must not block"
    note = plugin._transform_tool_result("terminal", {}, '{"output": "ok"}', session_id="s1", task_id="main")
    assert "Within the next 1-2 steps" in json.loads(note)["proactive_agent_note"]
    assert plugin._transform_tool_result("terminal", {}, '{"output": "ok"}', session_id="s1", task_id="main") is None

    plugin.secretary._turns["s1"]["start"] -= 60
    blocked = main_tool(plugin, "terminal", {"command": "ls"})
    assert blocked["action"] == "block" and ">120s" in blocked["message"]
    for allowed in ("read_file", "delegate_task", "memory", "process_manage"):
        args = {"tasks": [{"goal": "g", "context": GOOD_CONTEXT}]} if allowed == "delegate_task" else {}
        res = main_tool(plugin, allowed, args)
        assert res is None or res.get("action") != "block", allowed


def test_turn_budget_counts_tool_output(plugin, make_ctx):
    make_ctx(config={"turn_soft_output_chars": 100, "turn_hard_output_chars": 200})
    start_turn(plugin)
    main_post(plugin, "read_file", {"path": "/x"}, "a" * 150)
    assert plugin.secretary.zone("s1") == "yellow"
    main_post(plugin, "read_file", {"path": "/x"}, "a" * 100)
    assert main_tool(plugin, "patch", {"path": "/x"})["action"] == "block"


def test_new_turn_and_steer_reset_budget(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    plugin.secretary._turns["s1"]["start"] -= 200
    assert main_tool(plugin, "terminal", {"command": "ls"})["action"] == "block"
    steer = [{"role": "user", "content": "[OUT-OF-BAND USER MESSAGE] stop, do X instead"}]
    plugin._pre_api_request(session_id="s1", turn_id="t1", request_messages=steer)
    assert main_tool(plugin, "terminal", {"command": "ls"}) is None
    plugin.secretary._turns["s1"]["start"] -= 200
    start_turn(plugin, turn="t2")
    assert main_tool(plugin, "terminal", {"command": "ls"}, turn="t2") is None


def test_ungoverned_platforms_are_untouched(plugin, make_ctx):
    make_ctx()
    for platform in ("cli", "desktop", "telegram"):
        session = f"s-{platform}"
        start_turn(plugin, session=session, platform=platform)
        assert main_tool(plugin, "process_manage", {"action": "wait"}, session=session) is None
        assert main_tool(plugin, "terminal", {"command": "grep -rn x ~/.hermes"}, session=session) is None
        assert main_tool(plugin, "delegate_task", {"tasks": [{"goal": "g"}]}, session=session) is None


def test_cron_sessions_are_untouched(plugin, make_ctx):
    make_ctx()
    start_turn(plugin, session="cron_abc_1")
    assert main_tool(plugin, "process_manage", {"action": "wait"}, session="cron_abc_1") is None


def test_governed_platforms_from_env(plugin, make_ctx):
    make_ctx()
    with patch.dict(os.environ, {"PROACTIVE_GOVERNED_PLATFORMS": "telegram,slack"}):
        start_turn(plugin, session="tg", platform="telegram")
        assert main_tool(plugin, "process_manage", {"action": "wait"}, session="tg")["action"] == "block"
        start_turn(plugin, session="wx", platform="weixin")
        assert main_tool(plugin, "process_manage", {"action": "wait"}, session="wx") is None


def test_bool_settings_parse_from_env(plugin, make_ctx):
    make_ctx()
    with patch.dict(os.environ, {"PROACTIVE_WORKER_WRITE_GUARD": "false"}):
        assert plugin.config.get("worker_write_guard") is False


def test_main_blocks_wait_and_unbounded_scans(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    assert main_tool(plugin, "process_manage", {"action": "wait"})["action"] == "block"
    assert main_tool(plugin, "process_manage", {"action": "list"}) is None
    assert main_tool(plugin, "terminal", {"command": "grep -rnw '/home/ubuntu/.hermes' -e 'proactive'"})["action"] == "block"
    assert main_tool(plugin, "terminal", {"command": "nmap -p 1-10000 1.2.3.4"})["action"] == "block"
    assert main_tool(plugin, "terminal", {"command": "grep -rn TODO ./src"}) is None
    assert main_tool(plugin, "terminal", {"command": "rg proactive /home/ubuntu/project/plugins"}) is None


def test_yielded_terminal_result_explains_background(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    raw = json.dumps({"output": "Uploading 40%", "status": "yielded_to_background", "session_id": "proc_1"})
    out = json.loads(plugin._transform_tool_result("terminal", {}, raw, session_id="s1", task_id="main"))
    assert "NOT failed" in out["note"] and out["output"] == "Uploading 40%"


def test_plugin_errors_never_block_tools(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    with patch.object(plugin.secretary, "pre_tool_call", side_effect=RuntimeError("boom")):
        assert main_tool(plugin, "terminal", {"command": "ls"}) is None


# ---------------------------------------------------------------- handoff contract

def test_thin_handoff_returned_once_then_enriched(plugin, make_ctx):
    make_ctx()
    history = [{"role": "user", "content": "autosub 又 522 了，别自己改 edgetunnel 的代码"},
               {"role": "assistant", "content": "我看了，DNS 是代理状态"}]
    start_turn(plugin, history=history, user="帮我修一下")
    first = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "修复 522", "context": None}]})
    assert first["action"] == "block" and "Exact targets" in first["message"]

    second = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "修复 522", "context": None}]})
    assert second["action"] == "modify"
    task = second["args"]["tasks"][0]
    assert plugin.handoff.BRIEFING_MARKER in task["context"]
    assert "别自己改 edgetunnel" in task["context"], "fallback summary keeps the user's words"
    assert "Write the final report in Chinese." in task["context"]
    assert task["output_schema"]["required"] == ["status", "summary"]


def test_handoff_uses_llm_summary_and_memory(plugin, make_ctx, tmp_path):
    calls = []

    class FakeLLM:
        def complete(self, **kw):
            calls.append(kw)
            return SimpleNamespace(text="- User wants the 522 fixed without editing upstream code")

    (tmp_path / "memories").mkdir()
    (tmp_path / "memories" / "MEMORY.md").write_text("Never deploy with --env production", encoding="utf-8")
    fake_constants = SimpleNamespace(get_hermes_home=lambda: tmp_path)
    make_ctx(llm=FakeLLM())
    with patch.dict(sys.modules, {"hermes_constants": fake_constants}):
        start_turn(plugin, history=[{"role": "user", "content": "fix it"}])
        res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Fix 522", "context": GOOD_CONTEXT}]})
    ctx_text = res["args"]["tasks"][0]["context"]
    assert ctx_text.startswith(GOOD_CONTEXT)
    assert "without editing upstream code" in ctx_text
    assert "Never deploy with --env production" in ctx_text
    assert calls and calls[0]["timeout"] == 12.0 and calls[0]["task"] == plugin.handoff.AUX_TASK


def test_llm_failure_falls_back_to_extract(plugin, make_ctx):
    class BrokenLLM:
        def complete(self, **kw):
            raise TimeoutError("slow")

    make_ctx(llm=BrokenLLM())
    start_turn(plugin, history=[{"role": "user", "content": "the deploy script must stay bash"}])
    res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Fix", "context": GOOD_CONTEXT}]})
    assert "the deploy script must stay bash" in res["args"]["tasks"][0]["context"]


def test_risky_handoff_needs_rollback(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    risky = GOOD_CONTEXT + " Change sshd port to 36422 via ssh.socket override and update iptables."
    first = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Move SSH port", "context": risky}]})
    assert first["action"] == "block" and "Rollback path" in first["message"]

    start_turn(plugin, turn="t2")
    safe = risky + " Rollback: keep port 22 open until 36422 is verified from outside."
    res = plugin._pre_tool_call("delegate_task", {"tasks": [{"goal": "Move SSH port", "context": safe}]},
                                session_id="s1", turn_id="t2", task_id="main")
    assert res["action"] == "modify"
    assert "Risky changes are allowed only as described" in res["args"]["tasks"][0]["context"]


def test_turn_end_drops_per_turn_return_markers(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    assert main_tool(plugin, "delegate_task", {"tasks": [{"goal": "g", "context": "x"}]})["action"] == "block"
    plugin._on_turn_end(session_id="s1")
    assert not any(k.startswith("thin:") for k in plugin.handoff._returned.get("s1", set()))


def test_home_relative_paths_are_targets(plugin):
    targets = plugin.guards.targets_in("edit ~/.hermes/scripts/a.sh and /srv/app/x.toml")
    assert targets == {"~/.hermes/scripts/a.sh", "/srv/app/x.toml"}


def test_single_goal_shape(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    res = main_tool(plugin, "delegate_task", {"goal": "Fix", "context": GOOD_CONTEXT})
    assert res["action"] == "modify" and "tasks" not in res["args"]
    assert plugin.handoff.BRIEFING_MARKER in res["args"]["context"]


def test_list_and_steer_actions_pass_through(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    assert main_tool(plugin, "delegate_task", {"action": "list"}) is None
    assert main_tool(plugin, "delegate_task", {"action": "steer", "subagent_id": "sa-1", "message": "x"}) is None


# ---------------------------------------------------------------- workers

def test_worker_only_governed_under_governed_parent(plugin, make_ctx):
    make_ctx()
    start_turn(plugin, session="cli-s", platform="cli")
    plugin._subagent_start(parent_session_id="cli-s", child_session_id="c9", child_subagent_id="sa-9", child_goal="g")
    assert plugin.workers.lookup(task_id="sa-9") is None
    assert plugin._pre_tool_call("terminal", {"command": "nmap 1.2.3.4"}, task_id="sa-9", session_id="c9") is None


def test_worker_fast_fail_on_repeated_identical_step(plugin, make_ctx):
    posted = []
    make_ctx(post_message=lambda text: posted.append(text) or SimpleNamespace(ok=True))
    start_turn(plugin, user="又 522 了")
    worker = spawn_worker(plugin)
    curl = {"command": 'curl -sI "https://app.example.com/ping?_t=$RANDOM"'}
    for i in range(3):
        assert w_tool(plugin, "terminal", curl, api=f"r{i}") is None
        result = json.dumps({"output": f"HTTP/2 522 \ndate: Wed, 04 Nov 2026 12:24:3{i} GMT\ncf-ray: a55398{i}",
                             "exit_code": 0})
        w_post(plugin, "terminal", curl, result, api=f"r{i}")
    assert worker["stop_reason"] and "HTTP/2 522" in worker["stop_reason"]
    note = plugin._transform_tool_result("terminal", curl, '{"output": "x"}', **CHILD)
    assert "STOP" in json.loads(note)["proactive_agent_note"]
    blocked = w_tool(plugin, "terminal", {"command": "ls"}, api="r9")
    assert blocked["action"] == "block" and "final report" in blocked["message"]


def test_worker_fast_fail_on_same_failure_with_different_args(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    worker = spawn_worker(plugin)
    for i, port in enumerate((22, 2222, 36422)):
        args = {"command": f"ssh -p {port} ubuntu@10.0.0.1 true"}
        w_post(plugin, "terminal", args, json.dumps({"output": "", "error": "Connection refused", "exit_code": 255}),
               api=f"r{i}")
    assert worker["stop_reason"] and "same failure" in worker["stop_reason"]


def test_worker_step_budget(plugin, make_ctx):
    make_ctx(config={"worker_max_tool_rounds": 3})
    start_turn(plugin)
    spawn_worker(plugin)
    for i in range(3):
        assert w_tool(plugin, "read_file", {"path": f"/tmp/f{i}"}, api=f"r{i}") is None
    blocked = w_tool(plugin, "read_file", {"path": "/tmp/f9"}, api="r3")
    assert blocked["action"] == "block" and "step budget" in blocked["message"]


def test_worker_risky_and_scan_commands(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    assert w_tool(plugin, "terminal", {"command": "sudo iptables -I INPUT 1 -p tcp --dport 36422 -j ACCEPT"})["action"] == "block"
    assert w_tool(plugin, "terminal", {"command": "curl -X DELETE https://api.cloudflare.com/client/v4/zones/z/dns_records/r"})["action"] == "block"
    assert w_tool(plugin, "execute_code", {"code": "for p in range(1, 10000): s.connect_ex((ip, p))"})["action"] == "block"
    assert w_tool(plugin, "terminal", {"command": "npx wrangler deploy --config wrangler.toml"}) is None


def test_worker_risky_allowed_with_rollback_handoff(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    context = GOOD_CONTEXT + " Update iptables for 36422. Rollback: keep port 22 open until 36422 is verified."
    spawn_worker(plugin, goal="Move SSH port", context=context)
    assert w_tool(plugin, "terminal", {"command": "sudo iptables -I INPUT 1 -p tcp --dport 36422 -j ACCEPT"}) is None


def test_worker_write_guard_blocks_blind_overwrite(plugin, make_ctx, tmp_path):
    make_ctx()
    start_turn(plugin)
    worker = spawn_worker(plugin)
    target = tmp_path / "memory_autocommit.sh"
    target.write_text("#!/bin/bash\necho tested by another worker\n", encoding="utf-8")
    os.utime(target, (worker["spawn_wall"] + 5, worker["spawn_wall"] + 5))
    args = {"path": str(target), "content": "python version"}
    blocked = w_tool(plugin, "write_file", args)
    assert blocked["action"] == "block" and "changed by someone else" in blocked["message"]

    w_post(plugin, "read_file", {"path": str(target)}, '{"content": "..."}')
    assert w_tool(plugin, "write_file", args) is None


def test_worker_can_create_new_files(plugin, make_ctx, tmp_path):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    assert w_tool(plugin, "write_file", {"path": str(tmp_path / "new.txt"), "content": "x"}) is None


# ---------------------------------------------------------------- results, pivots, progress

def test_result_without_evidence_flags_verification(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    plugin._subagent_stop(parent_session_id="s1", child_session_id="child-1", child_status="completed",
                          child_summary="All fixed, 143 nodes OK", tool_call_history=[], duration_ms=120000)
    injected = start_turn(plugin, turn="t2")
    assert "no verifiable evidence" in injected["context"]
    assert start_turn(plugin, turn="t3") is None, "notes are delivered once"


def test_result_with_evidence_lists_it(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    summary = json.dumps({"status": "completed", "summary": "ok",
                          "evidence": ["curl -sI https://app.example.com/ping -> HTTP/2 200"]})
    plugin._subagent_stop(parent_session_id="s1", child_session_id="child-1", child_status="completed",
                          child_summary=summary, tool_call_history=[], duration_ms=60000)
    injected = start_turn(plugin, turn="t2")
    assert "HTTP/2 200" in injected["context"] and "Spot-check" in injected["context"]


def test_stopped_worker_result_is_reported_as_blocked(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    worker = spawn_worker(plugin)
    worker["stop_reason"] = "hit the same failure 3 times"
    plugin._subagent_stop(parent_session_id="s1", child_session_id="child-1", child_status="completed",
                          child_summary="done", tool_call_history=[], duration_ms=1000)
    assert "The plugin stopped it" in start_turn(plugin, turn="t2")["context"]


def test_turn_end_keeps_workers_and_notes(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    plugin._subagent_stop(parent_session_id="s1", child_session_id="child-1", child_status="completed",
                          child_summary="done", tool_call_history=[], duration_ms=1000)
    plugin._on_turn_end(session_id="s1", completed=True)
    assert "Check this subagent result" in start_turn(plugin, turn="t2")["context"]


def test_session_reset_clears_everything(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    plugin._on_session_clean(session_id="s1")
    assert start_turn(plugin, turn="t2") is None


def test_running_workers_are_listed_each_turn(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    assert "Subagents still running" in start_turn(plugin, turn="t2")["context"]


def test_pivot_on_same_targets_asks_to_stop_old_worker(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    ctx_a = GOOD_CONTEXT + " Write /home/ubuntu/.hermes/scripts/memory_autocommit.sh in Python."
    spawn_worker(plugin, goal="Python autocommit", context=ctx_a)
    ctx_b = GOOD_CONTEXT + " Write /home/ubuntu/.hermes/scripts/memory_autocommit.sh in pure Bash."
    first = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Bash autocommit", "context": ctx_b}]})
    assert first["action"] == "block" and "action='stop'" in first["message"] and "sa-0-abc" in first["message"]
    assert main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Bash autocommit", "context": ctx_b}]})["action"] == "modify"


def test_stopping_old_worker_clears_pivot(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    ctx_a = GOOD_CONTEXT + " Write /home/ubuntu/.hermes/scripts/memory_autocommit.sh in Python."
    spawn_worker(plugin, goal="Python autocommit", context=ctx_a)
    assert main_tool(plugin, "delegate_task", {"action": "stop", "subagent_id": "sa-0-abc"}) is None
    ctx_b = GOOD_CONTEXT + " Write /home/ubuntu/.hermes/scripts/memory_autocommit.sh in pure Bash."
    assert main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Bash", "context": ctx_b}]})["action"] == "modify"


def test_progress_tick_posts_to_chat(plugin, make_ctx):
    posted = []
    done = threading.Event()

    def post(text):
        posted.append(text)
        done.set()
        return SimpleNamespace(ok=True)

    make_ctx(post_message=post, config={"progress_interval_seconds": 3600})
    start_turn(plugin, user="帮我部署一下")
    spawn_worker(plugin)
    plugin.workers._progress_tick("s1")
    assert done.wait(2)
    assert posted[0].startswith("后台进展：1 个子任务在跑")


# ---------------------------------------------------------------- background detach

class _FakeEnv:
    def _wait_for_process(self, proc, timeout=120, **kwargs):
        return {"returncode": 0, "output": "done"}


def _install_patch(plugin):
    base = MagicMock()
    base.BaseEnvironment = _FakeEnv
    modules = {"tools": MagicMock(), "tools.environments": MagicMock(), "tools.environments.base": base,
               "tools.interrupt": MagicMock()}
    original = _FakeEnv._wait_for_process
    plugin.secretary._patched = False
    with patch.dict(sys.modules, modules):
        plugin.secretary.install_detach_patch()
    return original


def test_detach_timer_only_for_governed_main(plugin, make_ctx):
    make_ctx()
    original = _install_patch(plugin)
    try:
        start_turn(plugin, session="wx-1", platform="weixin")
        start_turn(plugin, session="cli-1", platform="cli")
        for session, expected in (("wx-1", True), ("cli-1", False)):
            with patch.dict(os.environ, {"HERMES_SESSION_ID": session}), patch("threading.Timer") as timer:
                res = _FakeEnv()._wait_for_process(MagicMock(), timeout=120, watch_interrupt_tid=1,
                                                   yield_handler=MagicMock())
                assert res["output"] == "done"
                assert timer.called is expected, session
                if expected:
                    assert timer.call_args[0][0] == 30.0
    finally:
        _FakeEnv._wait_for_process = original
        plugin.secretary._patched = False


def test_detach_leaves_a_foreign_yield_alone(plugin, make_ctx):
    make_ctx()
    original = _install_patch(plugin)
    interrupt = MagicMock()
    try:
        start_turn(plugin, session="wx-1", platform="weixin")
        with patch.dict(os.environ, {"HERMES_SESSION_ID": "wx-1"}), \
                patch.dict(sys.modules, {"tools": MagicMock(), "tools.interrupt": interrupt}), \
                patch("threading.Timer"):
            _FakeEnv()._wait_for_process(MagicMock(), timeout=120, watch_interrupt_tid=1, yield_handler=MagicMock())
        interrupt.consume_yield.assert_not_called()
    finally:
        _FakeEnv._wait_for_process = original
        plugin.secretary._patched = False


def test_detach_skipped_for_children(plugin, make_ctx):
    make_ctx()
    original = _install_patch(plugin)
    child_ctx = SimpleNamespace(is_delegated_child_context=lambda: True)
    try:
        start_turn(plugin, session="wx-1", platform="weixin")
        with patch.dict(os.environ, {"HERMES_SESSION_ID": "wx-1"}), \
                patch.dict(sys.modules, {"agent": MagicMock(), "agent.delegation_context": child_ctx}), \
                patch("threading.Timer") as timer:
            _FakeEnv()._wait_for_process(MagicMock(), timeout=120, watch_interrupt_tid=1, yield_handler=MagicMock())
            timer.assert_not_called()
    finally:
        _FakeEnv._wait_for_process = original
        plugin.secretary._patched = False
