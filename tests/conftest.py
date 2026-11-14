"""Load the plugin the way Hermes does: the plugin directory as a package with submodules."""

import importlib.util
import os
import sqlite3
import sys

import pytest

from helpers import PLUGIN_DIR

PACKAGE = "super_agent_under_test"


def _load():
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]
    spec = importlib.util.spec_from_file_location(
        PACKAGE, os.path.join(PLUGIN_DIR, "__init__.py"), submodule_search_locations=[PLUGIN_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module


class FakeContext:
    def __init__(self, config=None, post_message=None, inject_message=None):
        self.config = config or {}
        self.hooks = {}
        self.middleware = {}
        self.prompt_sections = {}
        self.cli_commands = {}
        if post_message is not None:
            self.post_message = post_message
        if inject_message is not None:
            self.inject_message = inject_message

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def register_hook(self, name, fn, **kwargs):
        self.hooks[name] = fn

    def register_middleware(self, kind, fn):
        self.middleware[kind] = fn

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli_commands[name] = {"help": help, "setup_fn": setup_fn, "handler_fn": handler_fn,
                                   "description": description}

    def register_system_prompt_section(self, name, renderer, position="after_memory", max_chars=2000):
        self.prompt_sections[name] = {"renderer": renderer, "position": position, "max_chars": max_chars}


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    # A real Hermes on sys.path would otherwise put the ledger under the developer's own home.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes_home"))
    for var in ("HERMES_SESSION_PLATFORM", "HERMES_PLATFORM", "HERMES_CRON_SESSION", "_HERMES_CRON_EXTERNAL_WORKER"):
        monkeypatch.delenv(var, raising=False)
    module = _load()
    module._reset_all()
    # register() reopens the ledger at its default path; without Hermes importable (as in CI) that is :memory:,
    # which preflight then reports. Point the default at the same test file.
    db_path = tmp_path / "ledger.db"
    monkeypatch.setattr(module.ledger, "_open_default", lambda: sqlite3.connect(str(db_path), check_same_thread=False))
    module.ledger.load(db_path)
    yield module
    module._reset_all()
    module.config.set_ctx(None)


@pytest.fixture
def make_ctx(plugin):
    def _make(**kwargs):
        ctx = FakeContext(**kwargs)
        plugin.register(ctx)
        return ctx
    return _make
