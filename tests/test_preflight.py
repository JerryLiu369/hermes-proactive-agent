"""Tests for startup host-capability self-check (Issue #10)."""

import builtins
import concurrent.futures
import sqlite3

import yaml

from helpers import start_turn


def _write_hermes_config(tmp_path, monkeypatch, data):
    home = tmp_path / "hermes_home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))


def test_clean_host_injects_nothing(plugin, make_ctx, tmp_path, monkeypatch):
    _write_hermes_config(tmp_path, monkeypatch, {
        "display": {"interim_assistant_messages": True},
        "delegation": {"max_iterations": 90},
    })
    make_ctx()
    assert start_turn(plugin, turn="t1") is None
    assert start_turn(plugin, turn="t2") is None


def test_memory_fallback_reported_once(plugin, make_ctx, tmp_path, monkeypatch):
    _write_hermes_config(tmp_path, monkeypatch, {
        "display": {"interim_assistant_messages": True},
        "delegation": {"max_iterations": 90},
    })
    monkeypatch.setattr(plugin.ledger, "_open_default", lambda: sqlite3.connect(":memory:", check_same_thread=False))
    make_ctx()
    assert plugin.ledger.is_memory_fallback() is True

    first = start_turn(plugin, turn="t1")
    assert first and ":memory:" in first["context"]
    assert start_turn(plugin, turn="t2") is None, "startup notice must be injected only once"


def test_explicit_ledger_path_never_triggers_fallback_warning(plugin, make_ctx, tmp_path, monkeypatch):
    _write_hermes_config(tmp_path, monkeypatch, {
        "display": {"interim_assistant_messages": True},
        "delegation": {"max_iterations": 90},
    })
    plugin.ledger.load(tmp_path / "explicit.db")
    assert plugin.ledger.is_memory_fallback() is False

    # Even if load(":memory:") is called explicitly in a unit test, it should not false-positive.
    plugin.ledger.load(":memory:")
    assert plugin.ledger.is_memory_fallback() is False


def test_ledger_open_error_reported_once(plugin, make_ctx, tmp_path, monkeypatch):
    _write_hermes_config(tmp_path, monkeypatch, {
        "display": {"interim_assistant_messages": True},
        "delegation": {"max_iterations": 90},
    })
    monkeypatch.setattr(plugin.reports, "recover_lost", lambda: (_ for _ in ()).throw(PermissionError("read-only fs")))
    make_ctx()

    first = start_turn(plugin, turn="t1")
    assert first and "Task ledger failed to open" in first["context"] and "read-only fs" in first["context"]
    assert start_turn(plugin, turn="t2") is None


def test_inline_delegation_detected_via_hermes_display_and_mode(plugin, make_ctx, tmp_path, monkeypatch):
    _write_hermes_config(tmp_path, monkeypatch, {
        "display": {"platforms": {"weixin": {"interim_assistant_messages": False}}},
        "delegation": {"max_iterations": 90},
    })
    make_ctx()

    first = start_turn(plugin, turn="t1")
    assert first
    assert "weixin" in first["context"] and "inline" in first["context"]
    assert "delegation.gateway_mode: background" in first["context"]
    assert start_turn(plugin, turn="t2") is None


def test_background_gateway_mode_overrides_disabled_interim(plugin, make_ctx, tmp_path, monkeypatch):
    _write_hermes_config(tmp_path, monkeypatch, {
        "display": {"interim_assistant_messages": False},
        "delegation": {"gateway_mode": "background", "max_iterations": 90},
    })
    make_ctx()
    assert start_turn(plugin, turn="t1") is None


def test_mattermost_without_override_warns_inline(plugin, make_ctx, tmp_path, monkeypatch):
    _write_hermes_config(tmp_path, monkeypatch, {
        "delegation": {"max_iterations": 90},
    })
    make_ctx(config={"governed_platforms": ["mattermost"]})

    first = start_turn(plugin, platform="mattermost", turn="t1")
    assert first and "mattermost" in first["context"] and "inline" in first["context"]


def test_high_or_unset_max_iterations_warns_once(plugin, make_ctx, tmp_path, monkeypatch):
    _write_hermes_config(tmp_path, monkeypatch, {
        "display": {"interim_assistant_messages": True},
        "delegation": {"max_iterations": 200},
    })
    make_ctx()

    first = start_turn(plugin, turn="t1")
    assert first and "delegation.max_iterations is 200" in first["context"] and "90" in first["context"]
    assert start_turn(plugin, turn="t2") is None


def test_ungoverned_or_child_does_not_consume_notice(plugin, make_ctx, tmp_path, monkeypatch):
    _write_hermes_config(tmp_path, monkeypatch, {
        "display": {"interim_assistant_messages": True},
        "delegation": {},
    })
    make_ctx()

    assert plugin._pre_llm_call(session_id="cli-1", turn_id="t1", platform="cli") is None
    assert plugin._pre_llm_call(session_id="child-1", task_id="sa-0-abc", turn_id="t1", platform="subagent") is None
    assert plugin._pre_llm_call(session_id="cron_1", task_id="cron:1:1", turn_id="t1", platform="cron") is None

    governed = start_turn(plugin, session="wx-1", platform="weixin", turn="t1")
    assert governed and "delegation.max_iterations is unset" in governed["context"]
    assert start_turn(plugin, session="wx-1", platform="weixin", turn="t2") is None


def test_pop_notice_is_thread_safe_and_delivered_once(plugin, make_ctx, tmp_path, monkeypatch):
    _write_hermes_config(tmp_path, monkeypatch, {"delegation": {"max_iterations": 250}})
    make_ctx()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: plugin.preflight.pop_notice(), range(8)))

    non_empty = [r for r in results if r is not None]
    assert len(non_empty) == 1
    assert "delegation.max_iterations" in non_empty[0]


def test_preflight_does_not_wake_or_pollute_reports(plugin, make_ctx, tmp_path, monkeypatch):
    woken = []
    _write_hermes_config(tmp_path, monkeypatch, {"delegation": {"max_iterations": 250}})
    make_ctx(inject_message=lambda msg, **kw: woken.append(msg) or True)

    notice = start_turn(plugin, turn="t1")
    assert notice and "delegation.max_iterations" in notice["context"]
    plugin._on_turn_end(session_id="s1", task_id="main", turn_id="t1", completed=True, failed=False, interrupted=False)
    assert woken == []
    assert start_turn(plugin, turn="t2") is None


def test_hermes_modules_unavailable_degrades_gracefully(plugin, make_ctx, monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith(("gateway.", "hermes_cli.", "tools.")):
            raise ImportError(f"no {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    issues = plugin.preflight.check_host()
    assert isinstance(issues, list)
