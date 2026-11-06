# AGENTS.md

Guidance for agents (and people) changing this repository. Read it before you change code or docs.

## What this is

`proactive-agent` is a Hermes Agent plugin. It runs the chat agent like a secretary with a team of workers: the
**main agent** (the secretary) stays reachable, decides and reports; **subagents** (workers, started with
`delegate_task`) do the slow work; every job ends with a result that is checked and recorded.

It exists because of five problems a chat agent has when it delegates on its own, and it keeps four promises, one
module each:

| Problem | Promise | Module |
|---|---|---|
| The main agent does long work itself and the chat goes silent | 1. The chat stays responsive | `secretary.py` |
| A subagent starts blind | 2. Subagents start informed | `handoff.py` |
| A stuck subagent repeats the same step | 3. Subagents do not spin | `workers.py` |
| "Done" is a self-report; what a subagent did is lost when it ends or Hermes restarts | 4. Results are checked and remembered | `reports.py`, `ledger.py` |

Supporting modules: `context.py` (who is calling: governed main agent, subagent or cron run), `prompt.py` (system
prompt section), `config.py` (settings), `util.py` (text and tool-result helpers), `__init__.py` (hook wiring).
`proactive-agent/` is the whole installed plugin; everything else in the repository is development only.

A new feature should fit one of the four promises. If it does not, question it before building it.

## Design rules

These are decisions the maintainer made. Do not change them without being asked.

- **Talk only to the main agent.** The plugin adds notes to the main agent's context (`pre_llm_call` context, tool
  result notes, `pre_tool_call` block messages). It never posts to the user's chat, and the main agent decides what
  the user hears.
- **No timed messages.** No progress lines, timers or periodic injections. Notes are triggered by events (a turn
  starts, a tool returns, a subagent ends) and delivered once.
- **No model calls.** Every check is plain code.
- **Hermes keeps its own job.** Before adding anything, check whether Hermes already does it (see "Already in
  Hermes" below). The plugin only fills gaps Hermes leaves open.
- **Never in the way.** Every hook is wrapped in `_guarded`: a plugin error is logged and the call proceeds.
- **Only where asked.** Only platforms in `governed_platforms` (default `["weixin"]`) and the subagents they spawn are
  governed. Cron runs and other platforms are untouched.
- **Verify, do not assume.** Hermes changes quickly. Every hook argument, tool name, status value and module path the
  code relies on must be checked against the Hermes source, not taken from memory, older code or docs. When you check
  a new fact, add it to the list below.

## Hermes facts the code relies on

Checked against the Hermes Agent v0.22.7 source. File paths are relative to the `hermes-agent` checkout.

**Hooks and their arguments**

- `pre_llm_call` (`agent/turn_context.py`): `session_id, task_id, turn_id, user_message, conversation_history,
  is_first_turn, model, platform, parent_session_id, sender_id`. A returned `{"context": str}` is added to the user
  message, not the system prompt.
- `pre_api_request` (`agent/turn_api_request.py`): includes `session_id, turn_id, platform, request_messages,
  conversation_history`. Fires before every model call, so it sees mid-turn user messages.
- `pre_tool_call` (`hermes_cli/plugins.py`): `tool_name, args, task_id, session_id, tool_call_id, turn_id,
  api_request_id`. **No `platform`**: the platform comes from `pre_llm_call` (`context.remember_platform`) or the
  session env. Directives: `{"action": "block", "message"}` (the message becomes the tool result) and
  `{"action": "modify", "args"}` (shallow-merged into the original args).
- `post_tool_call` and `transform_tool_result` (`model_tools.py`): add `result, duration_ms, status, error_type,
  error_message`. `status` is `"ok"` or `"error"`. The first string returned by `transform_tool_result` replaces the
  result.
- `subagent_start` (`tools/delegate_tool.py`): `parent_session_id, parent_turn_id, parent_subagent_id,
  child_session_id, child_subagent_id, child_role, child_goal`.
- `subagent_stop` (`tools/delegate_tool_results.py`): `parent_session_id, parent_turn_id, child_session_id,
  child_role, child_summary, child_status, tool_call_history, duration_ms`. **No subagent id**: the worker is found
  by `child_session_id`. `child_status` is `completed`, `interrupted` or `failed`. `tool_call_history` items carry
  `tool_name`, `tool_input.targets` (not full args) and `status` (`ok`, `error`, `unknown`).
- `on_session_end` (`agent/turn_finalizer.py`) fires after **every** `run_conversation`, i.e. every turn. Only
  per-turn state may be dropped there.
- `on_session_reset`: `/new` passes the new session as `session_id` and the ended one as `old_session_id`
  (`gateway/slash_commands_session.py`). Gateway shutdown fires it too (`gateway/run_shutdown_session_end.py`), so
  it must never delete the ledger.

**Who is calling**

- Subagent: task id `sa-<n>-<hex>`, platform `"subagent"` (`tools/delegate_tool.py`), and
  `agent.delegation_context.is_delegated_child_context()`.
- Cron: task id `cron:<job>:<run>`, session `cron_<job>_<time>` (`cron/scheduler.py`), the contextvars
  `cron.execution_identity.current_cron_execution()` and `gateway.session_cron.current_execution()`, and a truthy
  `HERMES_CRON_SESSION`.
- Session facts such as `HERMES_SESSION_ID` and `HERMES_SESSION_PLATFORM` are contextvars read with
  `gateway.session_context.get_session_env`; hook workers run in a copy of that context.

**Tools and delegation**

- Registered names used here: `delegate_task` (actions `spawn`, `list`, `steer`, `stop`), `process_manage`
  (including `wait`, `list`, `poll`, `log`, `kill`), `terminal`, `read_file`, `write_file`, `patch`,
  `search_files`, `session_search`, `memory`, `skill_view`, `skills_list`, `skill_manage`, `todo_list`, `clarify`.
  `send_message` and `todo` are not registered for the main agent. `tests/test_hermes_e2e.py` checks
  `secretary.RED_ALLOWED_TOOLS` against the live registry.
- The `delegate_task` dispatch result has `subagent_ids` and `live_transcripts` (`tools/delegate_tool_dispatch.py`).
- Subagents run with `skip_memory=True`, so `USER.md` and `MEMORY.md` reach them only through the handoff briefing.
  They cannot use `clarify`, `memory`, `send_message` or `cronjob_manage`. Hermes cannot resume a finished
  subagent; a follow-up is a new subagent.
- Mid-turn user messages carry `[OUT-OF-BAND USER MESSAGE` (`agent/prompt_builder.py`) or `display_kind: "steer"`.

**Other extension points**

- Background detach wraps `tools.environments.base.BaseEnvironment._wait_for_process(self, proc, timeout, *,
  bounded_capture, watch_interrupt_tid, output, yield_handler)` and calls `tools.interrupt.request_yield` /
  `consume_yield`. The terminal result then has `status: "yielded_to_background"` (`tools/terminal_tool.py`).
- `ctx.register_system_prompt_section(id, fn, *, position, max_chars)`: `fn` receives read-only session info, and the
  rendered text is frozen per session.
- `ctx.get_config(key)` reads `plugins.entries.proactive-agent.settings.<key>`.
- `plugins.plugin_storage.plugin_db(name, file)` opens the ledger's SQLite file under `plugin-data/`.

**Already in Hermes (do not rebuild)**

- Loop guard (`agent/tool_guardrails.py`, `tool_loop_guardrails`): warns on repeats. Hard stops are on only for
  unattended platforms, and `subagent` counts as attended, so subagents are only warned. That gap is why
  `workers.py` stops them.
- Step budget: `delegation.max_iterations`. Background vs inline delegation: `delegation.gateway_mode` (`auto` joins
  inline where interim assistant messages are off).
- Dangerous-command approvals (`approvals`; subagents resolve them non-interactively) and user shell hooks (`hooks`,
  with tool matchers, which can block `pre_tool_call`).
- Memory and skill nudges with a background review (`memory.nudge_interval`, `skills.creation_nudge_interval`).
- The bundled `security-guidance` plugin, which scans file writes.

## Working on the code

- State lives in module-level dicts guarded by a lock. Anything that must survive a restart goes in the ledger.
- Notes to the main agent or a subagent start with `[proactive-agent]` and are attached with `util.append_note`
  (JSON results stay valid JSON). Refusals use `util.block`.
- Hook entry points live in `__init__.py` and only route: worker calls go to `workers`, governed main-agent calls go to
  `secretary`, `handoff` or `reports`.
- Comments explain a reason the code cannot show (a Hermes behaviour, an invariant). Do not narrate the code.
- Ruff runs only syntax and pyflakes checks (`E9`, `F`); long prompt strings are intentional.

## Tests

```bash
uv run --group dev pytest
uv run --group dev ruff check
```

- One test file per module. `tests/helpers.py` calls the hooks with the same arguments Hermes passes (including
  `status` on `post_tool_call`); keep it faithful when Hermes changes.
- `tests/test_hermes_e2e.py` loads the plugin through Hermes' own plugin loader and is skipped when `hermes_cli` is
  not importable. Run it against a real Hermes checkout before a release:
  `PYTHONPATH=<deps with pytest and pyyaml>:<hermes-agent checkout> <hermes venv python> -m pytest -p no:cacheprovider tests`.
- `tests/test_manifest.py` enforces that `plugin.yaml` lists every registered hook, that its `config_schema` matches
  `config.DEFAULTS`, and that the three version strings agree.

## Docs

- `README.md`, `README_zh.md`, `llms.txt` and `llms-full.txt` say the same things and are updated together. Facts
  live in the READMEs and `llms-full.txt`; `llms.txt` is the compact index an answer engine reads first, so it carries
  the same numbers in short answers and then links out.
- `README.md` and `README_zh.md` follow llmstxt.org-style answer-first order: an answer block under 100 words, then the
  five problems, the four promises, the design rules, the settings, then the FAQ. Keep that order and keep the two
  files mirrored section by section.
- `llms.txt` follows the llmstxt.org v2 shape: H1, one blockquote summary, plain details (citable facts and direct
  answers, no headings), then `##` sections that are markdown link lists, with `## Optional` last. `llms-full.txt` is
  the self-contained reference (question index, comparison matrix, mechanism detail, settings, limits, FAQ, citation).
- Never add a claim to any doc that the code does not back. Every number in the docs must match `config.DEFAULTS`.
- Write plainly: short sentences, one term per concept, numbers that match the defaults in `config.py`.
- A changed setting means changing `config.DEFAULTS`, `plugin.yaml`'s `config_schema` and the settings tables in
  `README.md`, `README_zh.md` and `llms-full.txt`.

## Commits and releases

- Commit messages: `feat:`, `fix:`, `refactor:`, `docs:` or `release:`, then an imperative summary.
- Push, tag or publish only when the maintainer asks.
- A release bumps the version in `pyproject.toml`, `proactive-agent/plugin.yaml` and `proactive-agent/__init__.py`
  (`release: vX.Y.Z`), adds an annotated tag `vX.Y.Z`, and publishes a GitHub release. Its notes describe everything
  since the previous tag in user-facing terms.
