"""Manifest, settings and the system prompt section."""

import os
import re
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from helpers import PLUGIN_DIR, REPO_ROOT


def test_versions_and_manifest_match_code(plugin):
    with open(os.path.join(PLUGIN_DIR, "plugin.yaml"), encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    with open(os.path.join(REPO_ROOT, "pyproject.toml"), encoding="utf-8") as f:
        pyproject_version = re.search(r'^version = "([^"]+)"', f.read(), re.MULTILINE).group(1)
    assert manifest["version"] == plugin.__version__ == pyproject_version
    registered, middleware = {}, {}
    plugin.register(SimpleNamespace(register_hook=lambda n, fn: registered.setdefault(n, fn),
                                    register_middleware=lambda k, fn: middleware.setdefault(k, fn),
                                    register_system_prompt_section=lambda *a, **k: None, get_config=lambda k, d=None: d))
    assert set(manifest["provides_hooks"]) == set(registered)
    assert set(manifest["provides_middleware"]) == set(middleware)
    schema = manifest["config_schema"]
    assert set(schema) == set(plugin.config.DEFAULTS)
    for key, default in plugin.config.DEFAULTS.items():
        assert schema[key]["default"] == default, key


def test_prompt_only_for_governed_main(plugin, make_ctx):
    ctx = make_ctx()
    render = ctx.prompt_sections["super-agent.discipline"]["renderer"]
    text = render({"platform": "weixin"})
    assert "~60s" in text and "At 120s" in text and "30s" in text and "action='list'" in text
    for info in ({"platform": "cli"}, {"platform": "subagent"}, {"platform": "cron"},
                 {"platform": "weixin", "session_id": "cron_1"}, None, {}):
        assert render(info) == ""


def test_config_from_env(plugin, make_ctx):
    make_ctx(config={"worker_repeat_limit": 9})
    assert plugin.config.get("worker_repeat_limit") == 9
    assert plugin.config.get("handoff_min_context_chars") == 120
    with patch.dict(os.environ, {"PROACTIVE_WORKER_REPEAT_LIMIT": "7", "PROACTIVE_GOVERNED_PLATFORMS": "a, b"}):
        assert plugin.config.get("worker_repeat_limit") == 7
        assert plugin.config.governed_platforms() == ["a", "b"]
    with patch.dict(os.environ, {"SUPER_AGENT_WORKER_REPEAT_LIMIT": "8", "SUPER_AGENT_GOVERNED_PLATFORMS": "c, d"}):
        assert plugin.config.get("worker_repeat_limit") == 8
        assert plugin.config.governed_platforms() == ["c", "d"]
    with patch.dict(os.environ, {"SUPER_AGENT_WORKER_REPEAT_LIMIT": "8", "PROACTIVE_WORKER_REPEAT_LIMIT": "7",
                                 "SUPER_AGENT_GOVERNED_PLATFORMS": "c, d", "PROACTIVE_GOVERNED_PLATFORMS": "a, b"}):
        assert plugin.config.get("worker_repeat_limit") == 8
        assert plugin.config.governed_platforms() == ["c", "d"]
