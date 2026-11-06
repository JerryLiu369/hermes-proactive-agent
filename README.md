# Hermes Proactive Agent (`proactive-agent`)

[![Hermes Plugin](https://img.shields.io/badge/Hermes-Plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Version](https://img.shields.io/badge/version-0.1.11-green.svg)](https://github.com/JerryLiu369/hermes-proactive-agent)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)

[English](README.md) | [简体中文](README_zh.md)

---

`proactive-agent` is a [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that runs your chat agent like a
personal secretary with a team of workers.

You are the boss. The **main agent** is the secretary: it is always reachable, understands what you want, makes the
decisions and reports back. **Subagents** are the workers: they take the slow, long or noisy jobs.

## Design

The plugin follows one task through its life and does one simple thing at each step.

```
 You ──► Secretary (main agent) ──── delegate_task ────► Worker (subagent)
          1. Turn budget            2. Handoff            3. Worker limits
             ▲                                                 │
             └──────────── 4. Results and progress ◄───────────┘
```

1. **The secretary stays responsive.** It may investigate and think, but a turn has a time budget, and long commands
   run in the background.
2. **The secretary writes the brief.** It already knows the conversation, so it writes the handoff itself. The plugin
   only returns a brief that is clearly too short, and attaches what the worker cannot know: your recent messages, your
   memory and a few working rules.
3. **Workers do not spin.** A worker is stopped when it keeps repeating the same step. Its step budget is Hermes'
   own `delegation.max_iterations`.
4. **The loop closes.** The secretary hears how each worker ended and what to check, and you get progress lines while
   workers run.

Safety checks that Hermes already does are left to Hermes: subagents' dangerous commands are denied by default
(`delegation.subagent_auto_approve: false`), and file tools refuse stale overwrites.

## What it does

### 1. Turn budget

| Time in one turn (configurable) | Effect |
|---|---|
| under 60 s | nothing |
| from 60 s | a one-time note on the next tool result: answer the user or delegate within 1-2 steps |
| from 120 s | editing and execution tools are paused; read-only tools and `delegate_task` stay available |

- A message from you mid-turn resets the budget.
- Foreground commands longer than 30 s move to the background through Hermes' own yield path. The process keeps
  running and Hermes notifies the secretary when it finishes; the secretary is told the command has not failed.
- `process_manage(action='wait')` is refused: waiting on a background job would block the chat again.
- A short system prompt section explains this to the secretary up front.

### 2. Handoff

- A `delegate_task` whose `context` is under 120 characters is returned once per turn with a short checklist (what is
  known, exact targets, what must not change, when to stop, what proves success). The second attempt always goes
  through.
- Every accepted task gets, after the secretary's own context:
  - your last few messages, verbatim, so constraints like "don't touch X" reach the worker;
  - your `USER.md` and `MEMORY.md`, in full (Hermes does not load memory into subagents; it already caps both files);
  - working rules: stay in scope, stop after the same failure twice, risky changes only with a rollback path, check
    the real end state;
  - a default `output_schema` (`status`, `summary`, `evidence`, `blockers`) if the task had none.

No extra model call is made: the brief comes from the secretary, which already has the whole conversation cached.

### 3. Worker limits

Only for subagents spawned by a governed session.

- **Same step, same result.** The same tool with the same arguments returning the same result: after 3 repeats the
  worker gets a note to change approach; after 5 it is stopped. Its next tool calls are refused and it must report
  status `blocked`. You get a short alert in the chat.
- **Step budget.** Left to Hermes' `delegation.max_iterations`: when it runs out, Hermes gives the worker one last call
  without tools to write its report and tells the secretary the task was cut short.
- Workers keep their skills: the skills index and `skill_view` are available, like in the main agent.

### 4. Results and progress

- When a worker returns, the secretary's next turn gets a note: the evidence the worker reported, a warning that it
  claimed success without evidence, that it did not finish, or that the plugin stopped it.
- The secretary checks running workers on demand with `delegate_task(action='list')`, and the system prompt asks it to
  stop superseded ones (`action='stop'`). Nothing is injected into every turn.
- While workers run, a progress line is posted to the chat every 10 minutes (gateway platforms, via
  `ctx.post_message`, in your language).

### Scope

Only platforms in `governed_platforms` are affected (default `["weixin"]`). Other platforms, cron runs, and subagents
spawned by ungoverned sessions are untouched.

## Configuration

All keys live under `plugins.entries.proactive-agent.settings` in `~/.hermes/config.yaml`. Keys placed directly under
`proactive-agent:` are ignored by Hermes. Each key can also be overridden with an environment variable
`PROACTIVE_<KEY>` (upper case).

| Key | Default | Meaning |
|---|---|---|
| `governed_platforms` | `["weixin"]` | Platforms whose main agent (and its subagents) are governed |
| `terminal_auto_detach_seconds` | `30` | Foreground commands longer than this move to the background (0 disables) |
| `turn_soft_seconds` | `60` | When the secretary is reminded to answer or delegate |
| `turn_hard_seconds` | `120` | When editing and execution tools pause for the rest of the turn |
| `handoff_min_context_chars` | `120` | Shorter `context` is returned once per turn |
| `worker_repeat_limit` | `5` | Same step with the same result this many times stops a worker (note two repeats earlier) |
| `progress_interval_seconds` | `600` | Seconds between progress lines (0 disables progress lines and alerts) |

```yaml
plugins:
  entries:
    proactive-agent:
      settings:
        governed_platforms: ["weixin"]
        turn_soft_seconds: 60
        turn_hard_seconds: 120
```

## Install

```bash
hermes plugins install github:JerryLiu369/hermes-proactive-agent
hermes plugins enable proactive-agent
```

Recommended Hermes setting: lower the worker step budget. A worker that needs more than about 60 model calls is
usually stuck, and a lower cap makes it report sooner.

```yaml
delegation:
  max_iterations: 60
```

Manual install: clone into `~/.hermes/plugins/proactive-agent` and run `hermes plugins enable proactive-agent`.

## Development

```bash
pip install pytest pyyaml
pytest -q
```

The repo root is the plugin package; tests load it as a package (see `tests/conftest.py`).
`tests/test_hermes_e2e.py` runs against a real Hermes checkout when `hermes_cli` is importable and is skipped otherwise.

| Module | Role |
|---|---|
| `__init__.py` | hook wiring |
| `config.py` | settings |
| `context.py` | who is calling: governed main agent, worker, or cron |
| `secretary.py` | turn budget, background detach |
| `handoff.py` | `delegate_task` check and attached briefing |
| `workers.py` | worker limits, result notes, progress lines |
| `util.py` | text and tool-result helpers |
| `prompt.py` | system prompt section |

## Compatibility

- Hermes Agent with `pre_tool_call` arg rewriting, `subagent_start` / `subagent_stop` and `ctx.post_message`
  (tested against Hermes v0.22.7). Without `post_message` there are no progress lines.
- Python 3.10+.

## License

[MIT](LICENSE)
