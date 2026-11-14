"""delegate_task check and the attached briefing."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

from helpers import GOOD_CONTEXT, main_tool, start_turn


def test_thin_handoff_returned_once_then_briefed(plugin, make_ctx):
    make_ctx()
    history = [{"role": "user", "content": "autosub 又 522 了，别自己改 edgetunnel 的代码"},
               {"role": "assistant", "content": "我看了，DNS 是代理状态"}]
    start_turn(plugin, history=history, user="帮我修一下")
    first = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "修复 522", "context": None}]})
    assert first["action"] == "block" and "exact targets" in first["message"]

    second = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "修复 522", "context": None}]})
    assert second["action"] == "modify"
    task = second["args"]["tasks"][0]
    assert plugin.handoff.BRIEFING_MARKER in task["context"]
    assert "别自己改 edgetunnel" in task["context"] and "帮我修一下" in task["context"]
    assert "DNS 是代理状态" not in task["context"], "only the user's own words are attached"
    assert "final report in" not in task["context"]
    assert "## How to report" in task["context"]
    assert "output_schema" not in task, "no schema: Hermes would need jsonschema to check it"


def test_steer_joins_user_words_but_injected_context_does_not(plugin, make_ctx):
    make_ctx()
    start_turn(plugin, user="deploy the site")
    request = [{"role": "user", "content": "deploy the site\n\n[super-agent] Subagents still running: ..."},
               {"role": "user", "content": "[OUT-OF-BAND USER MESSAGE] use the staging config"}]
    plugin._pre_api_request(session_id="s1", turn_id="t1", request_messages=request)
    res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Deploy", "context": GOOD_CONTEXT}]})
    briefing = res["args"]["tasks"][0]["context"]
    assert "use the staging config" in briefing and "Subagents still running" not in briefing


def test_handoff_keeps_brief_and_attaches_memory(plugin, make_ctx, tmp_path):
    (tmp_path / "memories").mkdir()
    long_memory = "Never deploy with --env production. " + "x" * 9000 + " END-OF-MEMORY"
    (tmp_path / "memories" / "MEMORY.md").write_text(long_memory, encoding="utf-8")
    (tmp_path / "memories" / "USER.md").write_text("Prefers short answers", encoding="utf-8")
    make_ctx()
    with patch.dict(sys.modules, {"hermes_constants": SimpleNamespace(get_hermes_home=lambda: tmp_path)}):
        start_turn(plugin)
        res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Fix 522", "context": GOOD_CONTEXT,
                                                             "output_schema": {"type": "object"}}]})
    task = res["args"]["tasks"][0]
    assert task["context"].startswith(GOOD_CONTEXT)
    assert "Never deploy with --env production" in task["context"] and "END-OF-MEMORY" in task["context"]
    assert "Prefers short answers" in task["context"]
    assert task["output_schema"] == {"type": "object"}, "a schema the main agent set is left alone"


def test_single_goal_shape(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    res = main_tool(plugin, "delegate_task", {"goal": "Fix", "context": GOOD_CONTEXT})
    assert res["action"] == "modify" and "tasks" not in res["args"]
    assert plugin.handoff.BRIEFING_MARKER in res["args"]["context"]


def test_other_delegate_actions_pass_through(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    assert main_tool(plugin, "delegate_task", {"action": "list"}) is None
    assert main_tool(plugin, "delegate_task", {"action": "steer", "subagent_id": "sa-1", "message": "x"}) is None


def test_briefing_tells_the_worker_to_bring_a_decision_back(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    res = main_tool(plugin, "delegate_task", {"goal": "Fix", "context": GOOD_CONTEXT})
    context = res["args"]["context"]
    assert "You cannot ask the user" in context and "Do not guess" in context
    assert "do not retry it or reach the same result another way" in context
    assert "A refusal that says the user denied it may be automatic" in context
    assert '"needs_decision"' in context and '"question" and "options"' in context
    assert "output_schema" not in res["args"]


# ---------------------------------------------------------------- failed tasks since the user last wrote

def _fail(plugin, n, status="failed", summary="", turn="t1"):
    sub, child = f"sa-{n}-f", f"child-f{n}"
    res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": f"Fix the 522, attempt {n}", "context": GOOD_CONTEXT}]},
                    turn=turn)
    assert res["action"] == "modify", res
    plugin._subagent_start(parent_session_id="s1", child_session_id=child, child_subagent_id=sub,
                           child_goal=f"Fix the 522, attempt {n}")
    report = summary or json.dumps({"status": "blocked", "summary": "tried", "blockers": ["no Cloudflare token"]})
    plugin._subagent_stop(parent_session_id="s1", child_session_id=child, child_status=status,
                          child_summary=report, tool_call_history=[], duration_ms=1000)


def _dispatch(plugin, turn):
    return main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Fix the 522 again", "context": GOOD_CONTEXT}]},
                     turn=turn)


def test_dispatch_after_repeated_failures_goes_back_once(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    _fail(plugin, 0)
    _fail(plugin, 1)
    start_turn(plugin, turn="t2", user="[ASYNC DELEGATION BATCH COMPLETE — d1] a unit finished")
    first = _dispatch(plugin, "t2")
    assert first["action"] == "block", "an async result is not the user writing"
    assert "2 subagents in this chat ended without finishing" in first["message"]
    assert "no Cloudflare token" in first["message"] and "let them decide" in first["message"]
    assert _dispatch(plugin, "t2")["action"] == "modify", "returned once per turn"

    start_turn(plugin, turn="t3", user="use the API key in the vault")
    assert _dispatch(plugin, "t3")["action"] == "modify", "the user's answer resets the count"


def test_a_mid_turn_message_resets_the_failure_count(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    _fail(plugin, 0)
    _fail(plugin, 1)
    plugin._pre_api_request(session_id="s1", turn_id="t1",
                            conversation_history=[{"role": "user", "content": "[OUT-OF-BAND USER MESSAGE] try staging"}])
    assert _dispatch(plugin, "t1")["action"] == "modify"


def test_questions_and_reportless_ends_are_not_failures(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    _fail(plugin, 0, summary=json.dumps({"status": "needs_decision", "question": "Route or proxy?"}))
    _fail(plugin, 1, status="completed", summary="did some of it")
    _fail(plugin, 2)
    assert _dispatch(plugin, "t1")["action"] == "modify"


def test_failure_limit_zero_disables_it(plugin, make_ctx, monkeypatch):
    monkeypatch.setenv("SUPER_AGENT_FAILED_TASK_LIMIT", "0")
    make_ctx()
    start_turn(plugin)
    for n in range(3):
        _fail(plugin, n)
    assert _dispatch(plugin, "t1")["action"] == "modify"


def test_failures_count_only_once_the_user_wrote(plugin, make_ctx):
    """After a restart nothing says when the user last wrote, so earlier failures are not counted."""
    make_ctx()
    start_turn(plugin)
    _fail(plugin, 0)
    _fail(plugin, 1)
    plugin.handoff.forget_chat("s1")
    start_turn(plugin, turn="t2", user="[ASYNC DELEGATION BATCH COMPLETE — d1] a unit finished")
    assert _dispatch(plugin, "t2")["action"] == "modify"
