"""Reports: result notes for the main agent."""

import json

from helpers import GOOD_CONTEXT, end_turn, main_tool, spawn_worker, start_turn


def _stop(plugin, summary, status="completed"):
    plugin._subagent_stop(parent_session_id="s1", child_session_id="child-1", child_status=status,
                          child_summary=summary, tool_call_history=[], duration_ms=120000)


def test_result_without_evidence_asks_for_verification(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _stop(plugin, "All fixed, 143 nodes OK")
    injected = start_turn(plugin, turn="t2")
    assert "without evidence" in injected["context"]
    end_turn(plugin, turn="t2")
    assert start_turn(plugin, turn="t3") is None, "notes are delivered once"


def test_result_with_evidence_lists_it(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _stop(plugin, json.dumps({"status": "completed", "summary": "ok",
                              "evidence": ["curl -sI https://app.example.com/ping -> HTTP/2 200"]}))
    injected = start_turn(plugin, turn="t2")
    assert "HTTP/2 200" in injected["context"] and "Spot-check" in injected["context"]


def test_stopped_or_unfinished_results(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    worker = spawn_worker(plugin)
    worker["stop_reason"] = "ran the same step 5 times"
    _stop(plugin, "done")
    assert "The plugin stopped it" in start_turn(plugin, turn="t2")["context"]
    spawn_worker(plugin, sub_id="sa-1-def", child_session="child-2")
    plugin._subagent_stop(parent_session_id="s1", child_session_id="child-2", child_status="failed",
                          child_summary="no access", duration_ms=1000)
    assert "did not finish" in start_turn(plugin, turn="t3")["context"]


def test_turn_end_keeps_workers_and_notes(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _stop(plugin, "done")
    plugin._on_turn_end(session_id="s1", completed=True)
    assert "Subagent finished" in start_turn(plugin, turn="t2")["context"]


def test_session_reset_clears_everything(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _stop(plugin, "done")
    plugin._on_session_clean(session_id="s1")
    assert plugin.workers.lookup(task_id="sa-0-abc") is None
    assert "s1" not in plugin.secretary._turns and plugin.secretary.user_words("s1") == []
    assert plugin.context.platform_for(session_id="s1") == ""
    assert "Subagent finished" in start_turn(plugin, turn="t2")["context"], "pending notes outlive the reset"


# ---------------------------------------------------------------- results the user has not heard (issue #15)

SESSION_KEY = "agent:main:weixin:dm:u1"
WAKE_TURN = "[ASYNC DELEGATION BATCH COMPLETE — deleg_1]\nA background fan-out unit you dispatched earlier has finished"


def _injector(accept=True):
    calls = []

    def inject_message(content, role="user", *, session_key=None, origin=None):
        calls.append({"content": content, "role": role, "session_key": session_key})
        return accept
    return calls, inject_message


def _redirect_row(text):
    # agent/conversation_loop.py::_apply_active_turn_redirect
    return {"role": "user", "content": text, "api_content": (
        "[Context from the interrupted assistant response]\n[This response was interrupted by a user correction.]"
        f"\n\n{text}")}


def _dispatched(plugin, ids):
    # tools/delegate_tool_dispatch.py::_dispatched_payload
    result = json.dumps({"status": "dispatched", "mode": "background", "count": len(ids), "subagent_ids": ids})
    plugin._post_tool_call("delegate_task", {}, result, session_id="s1", turn_id="t1", task_id="main")


def _two_results(plugin):
    start_turn(plugin, user="clean the disk and rewrite the README")
    spawn_worker(plugin, goal="Clean up the disk", sub_id="sa-0-a", child_session="child-a")
    spawn_worker(plugin, goal="Rewrite the README", sub_id="sa-1-b", child_session="child-b")
    _dispatched(plugin, ["sa-0-a", "sa-1-b"])
    end_turn(plugin)
    for child in ("child-a", "child-b"):
        plugin._subagent_stop(parent_session_id="s1", child_session_id=child, child_status="completed",
                              child_summary=json.dumps({"status": "completed", "summary": "done",
                                                        "evidence": ["df -h: 41% used"]}),
                              tool_call_history=[], duration_ms=400000)


def _user_cuts_into_the_report(plugin, history, text="how do results come back, per batch or per task?"):
    history = history + [_redirect_row(text)]
    plugin._pre_api_request(session_id="s1", turn_id="t2", platform="weixin", conversation_history=history,
                            request_messages=[])
    return history


def test_user_message_during_a_report_wakes_the_main_agent_to_finish_it(plugin, make_ctx, monkeypatch):
    """The issue #15 timeline: results arrive, the user writes before they were reported, the reply
    answers only the user, so the main agent is woken to report them."""
    monkeypatch.setenv("HERMES_SESSION_KEY", SESSION_KEY)
    calls, inject = _injector()
    make_ctx(inject_message=inject)
    _two_results(plugin)

    history = [{"role": "user", "content": "clean the disk and rewrite the README"}]
    first = start_turn(plugin, turn="t2", user=WAKE_TURN, history=history)["context"]
    assert first.count("Subagent finished") == 2
    _user_cuts_into_the_report(plugin, history + [{"role": "user", "content": WAKE_TURN}])
    words = plugin.secretary.user_words("s1")
    assert words[-1].startswith("how do results come back") and not any(w.startswith("[ASYNC") for w in words)
    end_turn(plugin, turn="t2")

    assert len(calls) == 1 and calls[0]["session_key"] == SESSION_KEY
    assert calls[0]["content"].startswith("[super-agent] No new message from the user.")
    assert plugin.ledger.get("sa-0-a")["unreported"] and plugin.ledger.get("sa-1-b")["unreported"]

    woken = start_turn(plugin, turn="t3", user=calls[0]["content"])["context"]
    assert "has not heard these subagent results" in woken
    assert "Clean up the disk" in woken and "Rewrite the README" in woken
    assert not any(w.startswith(("[super-agent]", "[proactive-agent]")) for w in plugin.secretary.user_words("s1"))
    end_turn(plugin, turn="t3")
    assert not plugin.ledger.get("sa-0-a")["unreported"], "a clean reply settles the results"
    assert len(calls) == 1
    assert start_turn(plugin, turn="t4") is None


def test_tool_result_after_the_user_wrote_carries_the_results_in_the_same_turn(plugin, make_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_KEY", SESSION_KEY)
    calls, inject = _injector()
    make_ctx(inject_message=inject)
    _two_results(plugin)
    start_turn(plugin, turn="t2", user=WAKE_TURN)
    assert plugin._transform_tool_result("read_file", {}, '{"content": "x"}', session_id="s1", turn_id="t2",
                                         task_id="main") is None, "no note before the user wrote"
    _user_cuts_into_the_report(plugin, [])
    out = plugin._transform_tool_result("read_file", {}, '{"content": "x"}', session_id="s1", turn_id="t2",
                                        task_id="main")
    note = json.loads(out)["super_agent_note"]
    assert "has not heard" in note and "in the same reply" in note
    assert plugin._transform_tool_result("read_file", {}, '{"content": "x"}', session_id="s1", turn_id="t2",
                                         task_id="main") is None, "said once per user message"
    end_turn(plugin, turn="t2")
    assert calls == [] and not plugin.ledger.get("sa-1-b")["unreported"]


def test_interrupted_report_is_repeated_on_the_users_message(plugin, make_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_KEY", SESSION_KEY)
    calls, inject = _injector()
    make_ctx(inject_message=inject)
    _two_results(plugin)
    start_turn(plugin, turn="t2", user=WAKE_TURN)
    end_turn(plugin, turn="t2", completed=False, interrupted=True)
    assert calls == [], "Hermes runs the user's message next; no wake needed"
    note = start_turn(plugin, turn="t3", user="actually, what about the logs?")["context"]
    assert "has not heard" in note and "Answer the user's latest message" in note
    end_turn(plugin, turn="t3")
    assert start_turn(plugin, turn="t4") is None


def test_without_injection_the_next_user_message_carries_the_results(plugin, make_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_KEY", SESSION_KEY)
    calls, inject = _injector(accept=False)
    make_ctx(inject_message=inject)
    _two_results(plugin)
    start_turn(plugin, turn="t2", user=WAKE_TURN)
    _user_cuts_into_the_report(plugin, [])
    end_turn(plugin, turn="t2")
    assert len(calls) == 1 and plugin.ledger.get("sa-0-a")["wakes"] == 0
    assert "has not heard" in start_turn(plugin, turn="t3", user="so did it finish?")["context"]


def test_wakes_are_capped_and_a_late_wake_stays_silent(plugin, make_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_KEY", SESSION_KEY)
    calls, inject = _injector()
    make_ctx(inject_message=inject)
    _two_results(plugin)
    for turn in ("t2", "t3", "t4"):
        start_turn(plugin, turn=turn, user=WAKE_TURN)
        plugin._pre_api_request(session_id="s1", turn_id=turn, platform="weixin",
                                conversation_history=[_redirect_row(f"question during {turn}")])
        end_turn(plugin, turn=turn)
    assert len(calls) == 2, "at most two wakes per result"

    start_turn(plugin, turn="t5", user="and now?")
    end_turn(plugin, turn="t5")
    late = start_turn(plugin, turn="t6", user=calls[-1]["content"])["context"]
    assert "Nothing is left to report" in late and "[SILENT]" in late


def test_a_task_the_main_agent_stopped_is_not_news(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    main_tool(plugin, "delegate_task", {"action": "stop", "subagent_id": "sa-0-abc"})
    end_turn(plugin)
    _stop(plugin, "Interrupted", status="interrupted")
    assert "You stopped it" in start_turn(plugin, turn="t2")["context"]
    end_turn(plugin, turn="t2", completed=False, interrupted=True)
    assert start_turn(plugin, turn="t3") is None


def test_unreported_results_survive_a_restart(plugin, make_ctx, tmp_path):
    path = tmp_path / "unreported.db"
    plugin.ledger.load(path)
    make_ctx()
    plugin.ledger.load(path)
    start_turn(plugin)
    spawn_worker(plugin)
    _dispatched(plugin, ["sa-0-abc"])
    _stop(plugin, json.dumps({"status": "completed", "summary": "migrated", "evidence": ["rows: 120"]}))
    start_turn(plugin, turn="t2")
    plugin._reset_all()  # Hermes stopped before the turn ended
    plugin.ledger.load(path)
    note = start_turn(plugin, turn="t3")["context"]
    assert "has not heard" in note and "migrated" in note


def test_inline_results_are_not_chased(plugin, make_ctx, monkeypatch):
    """An inline subagent's result is the delegate_task tool result of the turn that is already replying."""
    monkeypatch.setenv("HERMES_SESSION_KEY", SESSION_KEY)
    calls, inject = _injector()
    make_ctx(inject_message=inject)
    start_turn(plugin)
    spawn_worker(plugin)
    _stop(plugin, json.dumps({"status": "completed", "summary": "ok", "evidence": ["HTTP/2 200"]}))
    end_turn(plugin)
    start_turn(plugin, turn="t2")
    _user_cuts_into_the_report(plugin, [])
    end_turn(plugin, turn="t2")
    assert calls == [] and not plugin.ledger.get("sa-0-abc")["unreported"]


def test_result_that_ends_before_the_dispatch_returns_is_still_chased(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _stop(plugin, json.dumps({"status": "completed", "summary": "quick", "evidence": ["ok"]}))
    _dispatched(plugin, ["sa-0-abc"])
    assert plugin.ledger.get("sa-0-abc")["unreported"]

def test_a_wake_that_brings_other_news_is_not_told_to_stay_silent(plugin, make_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_KEY", SESSION_KEY)
    calls, inject = _injector()
    make_ctx(inject_message=inject)
    _two_results(plugin)
    start_turn(plugin, turn="t2", user=WAKE_TURN)
    _user_cuts_into_the_report(plugin, [])
    end_turn(plugin, turn="t2")
    start_turn(plugin, turn="t3", user="ok, and the README?")
    end_turn(plugin, turn="t3")  # the user's own turn reported them before the queued wake ran
    plugin.ledger.add_note("s1", "[super-agent] Subagent finished: \"Rotate the logs\" (status: failed).")
    woken = start_turn(plugin, turn="t4", user=calls[0]["content"])["context"]
    assert "Rotate the logs" in woken and "[SILENT]" not in woken


# ---------------------------------------------------------------- a decision the subagent cannot make (issue #7)

QUESTION = {"status": "needs_decision", "summary": "Two ways to fix the 522: the worker route or the DNS proxy.",
            "question": "Change the worker route or turn off the DNS proxy?",
            "options": ["Worker route: no downtime, needs a redeploy", "DNS proxy: instant, exposes the origin IP"]}


def _asks(plugin, child="child-1", report=None):
    plugin._subagent_stop(parent_session_id="s1", child_session_id=child, child_status="completed",
                          child_summary=report if isinstance(report, str) else json.dumps(report or QUESTION),
                          tool_call_history=[], duration_ms=90000)


def _background_question(plugin):
    start_turn(plugin, user="fix the 522")
    spawn_worker(plugin)
    _dispatched(plugin, ["sa-0-abc"])
    end_turn(plugin)
    _asks(plugin)


def test_a_question_goes_to_the_user_and_the_answer_is_followed_up_once(plugin, make_ctx):
    make_ctx()
    _background_question(plugin)
    task = plugin.ledger.get("sa-0-abc")
    assert task["status"] == "needs_decision" and task["question"] == QUESTION["question"]
    assert task["options"] == QUESTION["options"]

    note = start_turn(plugin, turn="t2", user=WAKE_TURN)["context"]
    assert "needs a decision: Change the worker route or turn off the DNS proxy?" in note
    assert "Options: Worker route: no downtime" in note and "answer it yourself" in note
    assert "ask the user in one short message" in note
    assert "dispatch a new subagent with the answer" in note
    end_turn(plugin, turn="t2")

    reminder = start_turn(plugin, turn="t3", user="走 worker route 吧")["context"]
    assert "question is still open" in reminder and QUESTION["question"] in reminder
    end_turn(plugin, turn="t3")
    assert start_turn(plugin, turn="t4", user="还有别的吗") is None, "the reminder is delivered once"


def test_the_main_agent_can_answer_the_question_itself(plugin, make_ctx):
    """The chat may already settle what the subagent could not see: answering and dispatching in the
    turn that showed the question needs no reminder."""
    make_ctx()
    _background_question(plugin)
    note = start_turn(plugin, turn="t2", user=WAKE_TURN)["context"]
    assert "If the chat, memory or facts you can check settle it, answer it yourself" in note
    main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Fix the 522 via the worker route (you said no downtime)",
                                                   "context": GOOD_CONTEXT}]}, turn="t2")
    end_turn(plugin, turn="t2")
    assert start_turn(plugin, turn="t3", user="好的") is None


def test_dispatching_again_ends_the_wait(plugin, make_ctx):
    make_ctx()
    start_turn(plugin, user="fix the 522")
    spawn_worker(plugin)
    _asks(plugin)
    end_turn(plugin)
    start_turn(plugin, turn="t2", user="worker route")
    assert plugin.ledger.awaiting_decision("s1")
    res = main_tool(plugin, "delegate_task", {"tasks": [{"goal": "Fix the 522 via the worker route",
                                                         "context": GOOD_CONTEXT}]}, turn="t2")
    assert "It asked: Change the worker route" in res["args"]["tasks"][0]["context"]
    assert plugin.ledger.awaiting_decision("s1") == []
    end_turn(plugin, turn="t2")
    assert start_turn(plugin, turn="t3", user="how is it going?") is None


def test_an_inline_question_is_followed_up_on_the_users_next_message(plugin, make_ctx):
    """An inline result is the delegate_task result: the main agent asks in that turn, and the
    plugin's note arrives on the user's reply, so the reminder waits for the message after it."""
    make_ctx()
    start_turn(plugin, user="fix the 522")
    spawn_worker(plugin)
    _asks(plugin)
    end_turn(plugin)
    note = start_turn(plugin, turn="t2", user="hmm, which is safer?")["context"]
    assert "needs a decision" in note and "question is still open" not in note
    end_turn(plugin, turn="t2")
    assert "question is still open" in start_turn(plugin, turn="t3", user="ok, DNS proxy")["context"]
    end_turn(plugin, turn="t3")
    assert start_turn(plugin, turn="t4", user="thanks") is None


def test_the_follow_up_waits_for_the_users_own_message(plugin, make_ctx):
    make_ctx()
    _background_question(plugin)
    start_turn(plugin, turn="t2", user=WAKE_TURN)
    end_turn(plugin, turn="t2")
    plugin.ledger.add_note("s1", "[super-agent] Subagent finished: \"Rotate the logs\" (status: completed).")
    other = start_turn(plugin, turn="t3", user=WAKE_TURN)["context"]
    assert "Rotate the logs" in other and "question is still open" not in other
    end_turn(plugin, turn="t3")
    assert "question is still open" in start_turn(plugin, turn="t4", user="DNS proxy")["context"]


def test_a_question_the_user_has_not_heard_is_repeated_with_the_question(plugin, make_ctx):
    make_ctx()
    _background_question(plugin)
    start_turn(plugin, turn="t2", user=WAKE_TURN)
    end_turn(plugin, turn="t2", completed=False, interrupted=True)
    note = start_turn(plugin, turn="t3", user="what happened with the 522?")["context"]
    assert "has not heard" in note and "Its question: Change the worker route" in note
    assert "question is still open" not in note, "the user hears the question first"
    end_turn(plugin, turn="t3")
    assert "question is still open" in start_turn(plugin, turn="t4", user="worker route")["context"]


def test_other_words_for_a_decision_and_a_missing_question(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _asks(plugin, report={"status": "needs_input", "summary": "need a call", "question": "Which region?",
                          "options": [{"label": "eu", "consequence": "slower for US users"}]})
    task = plugin.ledger.get("sa-0-abc")
    assert task["status"] == "needs_decision" and task["options"] == ["eu - slower for US users"]
    spawn_worker(plugin, sub_id="sa-1", child_session="child-2")
    _asks(plugin, child="child-2", report={"status": "needs_decision", "summary": "unsure"})
    assert plugin.ledger.get("sa-1")["awaiting"] is False
    note = start_turn(plugin, turn="t2")["context"]
    assert "Which region? Options: eu - slower for US users." in note and "asked no clear question" in note


def test_a_question_is_kept_only_for_a_decision(plugin, make_ctx):
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _asks(plugin, report={"status": "blocked", "summary": "no token", "question": "Can you add a token?"})
    task = plugin.ledger.get("sa-0-abc")
    assert task["status"] == "blocked" and task["question"] == "" and not task["awaiting"]


def test_a_report_in_prose_or_a_fenced_block(plugin, make_ctx):
    """No output_schema is set, so a worker may wrap its JSON in prose or skip it."""
    make_ctx()
    start_turn(plugin)
    spawn_worker(plugin)
    _asks(plugin, report="Checked both paths.\n\n```json\n" + json.dumps(QUESTION) + "\n```\n")
    task = plugin.ledger.get("sa-0-abc")
    assert task["status"] == "needs_decision" and task["question"] == QUESTION["question"]
    spawn_worker(plugin, sub_id="sa-1", child_session="child-2")
    _asks(plugin, child="child-2", report="Status: needs_decision. Route or proxy? The user has to pick.")
    task = plugin.ledger.get("sa-1")
    assert task["status"] == "needs_decision" and task["summary"].startswith("Status: needs_decision")
    spawn_worker(plugin, sub_id="sa-2", child_session="child-3")
    _asks(plugin, child="child-3", report='Done. The config now reads {"status": "on"}.')
    assert plugin.ledger.get("sa-2")["status"] == "completed", "prose quoting JSON is not a report"
