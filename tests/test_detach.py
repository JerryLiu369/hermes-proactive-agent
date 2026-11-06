"""Background detach of long foreground commands."""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from helpers import start_turn


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


def _wait(**kw):
    return _FakeEnv()._wait_for_process(MagicMock(), timeout=120, watch_interrupt_tid=1, yield_handler=MagicMock())


def test_detach_timer_only_for_governed_main(plugin, make_ctx):
    make_ctx()
    original = _install_patch(plugin)
    try:
        start_turn(plugin, session="wx-1", platform="weixin")
        start_turn(plugin, session="cli-1", platform="cli")
        for session, expected in (("wx-1", True), ("cli-1", False)):
            with patch.dict(os.environ, {"HERMES_SESSION_ID": session}), patch("threading.Timer") as timer:
                assert _wait()["output"] == "done"
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
            _wait()
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
            _wait()
            timer.assert_not_called()
    finally:
        _FakeEnv._wait_for_process = original
        plugin.secretary._patched = False


def test_detach_fires_a_yield_and_consumes_it(plugin, make_ctx):
    make_ctx()
    original = _install_patch(plugin)
    interrupt = MagicMock()
    timers = []

    class _Timer:
        def __init__(self, delay, fn):
            self.fn = fn
            timers.append(self)
            self.daemon = False

        def start(self):
            pass

        def cancel(self):
            pass

    proc = MagicMock()
    proc.poll.return_value = None

    def wait(self, proc, timeout=120, **kwargs):
        timers[0].fn()  # the command outlives the detach delay while the original wait runs
        return {"returncode": 0, "output": "partial"}

    _FakeEnv._wait_for_process = wait
    plugin.secretary._patched = False
    try:
        with patch.dict(sys.modules, {"tools": MagicMock(), "tools.environments": MagicMock(),
                                      "tools.environments.base": MagicMock(BaseEnvironment=_FakeEnv),
                                      "tools.interrupt": interrupt}):
            plugin.secretary.install_detach_patch()
            start_turn(plugin, session="wx-1", platform="weixin")
            with patch.dict(os.environ, {"HERMES_SESSION_ID": "wx-1"}), patch("threading.Timer", _Timer):
                _FakeEnv()._wait_for_process(proc, timeout=120, watch_interrupt_tid=7, yield_handler=MagicMock())
        interrupt.request_yield.assert_called_once_with(7)
        interrupt.consume_yield.assert_called_once_with(7)
    finally:
        _FakeEnv._wait_for_process = original
        plugin.secretary._patched = False
