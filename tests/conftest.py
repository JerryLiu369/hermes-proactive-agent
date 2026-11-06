"""Load the plugin the way Hermes does: the plugin directory as a package with submodules."""

import importlib.util
import os
import sys

import pytest

from helpers import PLUGIN_DIR

PACKAGE = "proactive_agent_under_test"


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
    def __init__(self, config=None, post_message=None):
        self.config = config or {}
        self.hooks = {}
        self.prompt_sections = {}
        if post_message is not None:
            self.post_message = post_message

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def register_system_prompt_section(self, name, renderer, position="after_memory", max_chars=2000):
        self.prompt_sections[name] = {"renderer": renderer, "position": position, "max_chars": max_chars}


@pytest.fixture
def plugin():
    module = _load()
    module._on_session_clean()
    yield module
    module._on_session_clean()
    module.config.set_ctx(None)


@pytest.fixture
def make_ctx(plugin):
    def _make(**kwargs):
        ctx = FakeContext(**kwargs)
        plugin.register(ctx)
        return ctx
    return _make
