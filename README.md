# Hermes Proactive Agent

[![Release](https://img.shields.io/github/v/release/JerryLiu369/hermes-proactive-agent?label=release)](https://github.com/JerryLiu369/hermes-proactive-agent/releases)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![Hermes Plugin](https://img.shields.io/badge/Hermes-plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

English | [简体中文](README_zh.md)

A plugin for [Hermes Agent](https://hermes-agent.nousresearch.com) that makes the chat agent work like a good secretary. The main agent stays reachable while subagents do the slow work, and every job ends with a result that has been checked, has reached you, and is written down.

It is meant for talking to Hermes from a messaging app (WeChat by default). It makes no model calls of its own and never writes into your chat; what you hear still comes from the main agent.

## Install

```bash
hermes plugins install JerryLiu369/hermes-proactive-agent/proactive-agent
hermes plugins enable proactive-agent
```

Two optional settings are worth adding to `~/.hermes/config.yaml`. Restart the gateway afterwards:

```yaml
delegation:
  max_iterations: 60                # a subagent that needs more steps is usually stuck
plugins:
  entries:
    proactive-agent:
      allow_gateway_injection: true # lets the plugin wake the main agent to finish a report you interrupted
```

Without `allow_gateway_injection`, a report you interrupted simply waits for your next message. If you installed the plugin some other way, use the id that `hermes plugins list` shows instead of `proactive-agent`.

## Why

Hermes can already hand work to subagents that run in the background. Used from a chat, though, a few things keep going wrong:

- The main agent starts a build or a long debugging session itself, and the chat goes quiet for ten minutes.
- A subagent starts with no idea what you said, what has been tried, or what it must not touch.
- A stuck subagent runs the same command over and over until its step budget is gone. Or it reaches a choice that is not its to make, and guesses.
- "Done" only means the subagent said so.
- A result comes in just as you send a message. The main agent answers your message, and the result is never mentioned.
- When a subagent ends, or Hermes restarts, what it did is gone.

The plugin deals with each of these. The sections below go through them in order.

## What it does

### The chat stays responsive

Every turn of the main agent has a time budget. At 60 seconds it gets one reminder to answer you, or to hand the rest to a subagent, within a step or two. At 120 seconds, editing and running commands are paused for the rest of the turn. It can still read, delegate and wrap up: `memory`, the skill tools, `todo_list`, `clarify`, and `process_manage` to list, poll, read the log of or kill a background job. If you send a message in the middle of a turn, the clock starts over.

Long commands do not hold the turn either. A terminal command that is still running after 30 seconds moves to the background through Hermes' own yield mechanism. It keeps running, Hermes tells the main agent when it ends, and the main agent is told that the command has not failed. Waiting on a background job with `process_manage(action='wait')` is refused, because that would freeze the chat again.

### Subagents start informed

A subagent cannot see the chat and has no memory, so the main agent writes its brief. A brief is sent back once per turn, with a note on what is missing, if its `context` is under 120 characters or if it names a file that another running subagent is changing. The second attempt always goes through.

Every brief that goes through gets a briefing attached after the main agent's own text:

- your last 6 messages, word for word, so that "don't touch X" arrives intact;
- `USER.md` and `MEMORY.md`, which Hermes does not load for subagents;
- the other subagents running now, and the files they are changing;
- this chat's recent unfinished tasks: what they found, what blocked them, the corrections they got, the files they changed and where their log is;
- a few working rules: stay in scope, stop after the same failure twice, do not guess and do not work around a refusal, keep risky changes reversible, and check the real end state before reporting success;
- a default result format (`output_schema`): `status` (`completed`, `partial`, `blocked`, `needs_decision` or `failed`), `summary`, `evidence` and `blockers`, plus `question` and `options` when it needs a decision. A task that brings its own format keeps it.

### Subagents do not spin or guess

Hermes notices when a subagent repeats itself, but for subagents it only warns. The plugin counts. When a subagent runs the same tool with the same arguments and gets the same result, it is warned the third time and stopped the fifth. Repeats count over the whole run, not just back to back, and results are compared by their first line with long numbers and hashes masked, so a fresh request id does not make a result new. Once stopped, its tool calls are refused and it has to report `blocked`. Its total step budget stays with Hermes (`delegation.max_iterations`).

A subagent also cannot ask you anything. Hermes gives it no `clarify` tool and cannot resume it once it ends. So when it reaches a choice that is not its to make (two workable approaches, a production change, spending money, deleting data) and the brief does not settle it, it stops and reports `needs_decision` with one question and the options. Hermes checks the report against the format and asks once more if the question is missing.

The same happens when Hermes' approval or one of your hooks refuses a step, or when a step waits for an approval nobody can give during the run. The subagent is told right then (for a hook, on its next tool result) not to retry it or find another way to the same result, and to bring the question back instead. A refusal that says "user denied" may be automatic: in some setups Hermes denies a subagent's dangerous commands without asking anyone.

The main agent sees the whole chat, so it often knows the answer already and simply carries on. If it really is your call (a preference, a risk, money, a refused command), it asks you in one short message with the options. Either way it then sends a new subagent with the answer and the earlier task's record. If you have heard the question but the main agent has not followed up by your next message, that message reminds it once.

### Results are checked, reach you, and are remembered

When a subagent finishes, the main agent's next turn starts with a note about it: the evidence the subagent gave, or a warning that it claimed success without any, did not finish, or was stopped for repeating itself. In that last case the main agent tells you in one line what got stuck and what it will do instead. A task the main agent stopped on purpose is noted as such, not as a failure.

A background result arrives in a turn of its own. If you write while that turn is running, Hermes folds your message into it, and the main agent tends to answer only you. So the plugin keeps the result marked as unreported until a turn that showed it ends with a reply you did not interrupt:

- if the main agent calls a tool after your message, the tool's result reminds it to answer you and report the result in the same reply;
- if it replies without calling a tool, the plugin wakes it once more for a short follow-up, or for silence if it already covered the result. The wake goes through Hermes' message injection, which queues it behind any running turn, so it never cuts in;
- if Hermes cut the turn off instead, or waking is not allowed, your next message carries the reminder.

Each message you send in between gets its own reminder, so writing several times does not lose the result. A result gets at most 2 wakes, and one older than a day is no longer chased.

Every task is written to a SQLite ledger: its goal and status, the files it changed, corrections sent with `action='steer'`, its live transcript path, how it ended (summary, evidence, blockers and any question) and whether you have heard the result. Hermes cannot resume a finished subagent, so the next subagent in the chat gets the unfinished task's record instead. If Hermes restarts in the middle of a task, the main agent is told which tasks were lost, that their side effects are unknown and which files they had touched. Pending notes and unreported results live in the ledger too, so a restart does not drop them.

The ledger is at `<hermes home>/plugin-data/proactive-agent/ledger.db`. It keeps each chat's last 20 finished tasks, for up to 7 days.

## Principles

- **It talks only to the main agent.** It adds notes to the main agent's context, and to finish a report you interrupted it wakes the main agent with an internal message. It never posts to your chat; the main agent decides what you hear.
- **Nothing runs on a timer.** There are no progress messages. Every note is set off by something that happened (a turn starts, a tool returns, a subagent ends, a turn ends with a result unreported) and is delivered once.
- **No model calls.** Every check is plain code.
- **Hermes keeps its own jobs.** Command approval, stale-file protection, step budgets, loop warnings and handing finished results to the main agent all stay with Hermes. The plugin only fills the gaps.
- **It never gets in the way.** If the plugin itself fails, the error is logged and the tool call goes ahead.
- **It only acts where you ask.** Only the platforms in `governed_platforms` (default `["weixin"]`) and the subagents they start are governed. Other platforms and cron jobs are left alone.

## Settings

Settings go under `plugins.entries.proactive-agent.settings` in `~/.hermes/config.yaml`:

```yaml
plugins:
  entries:
    proactive-agent:
      allow_gateway_injection: true   # Hermes' permission, see Install
      settings:
        governed_platforms: ["weixin"]
        turn_soft_seconds: 60
        turn_hard_seconds: 120
        terminal_auto_detach_seconds: 30
        handoff_min_context_chars: 120
        worker_repeat_limit: 5
```

| Key | Default | What it does |
|---|---|---|
| `governed_platforms` | `["weixin"]` | Platforms whose main agent, and the subagents it starts, are governed |
| `turn_soft_seconds` | `60` | When the main agent is reminded to answer or delegate |
| `turn_hard_seconds` | `120` | When editing and running commands pause for the rest of the turn |
| `terminal_auto_detach_seconds` | `30` | How long a terminal command may run before it moves to the background (`0` turns this off) |
| `handoff_min_context_chars` | `120` | Briefs with a shorter `context` are sent back once per turn |
| `worker_repeat_limit` | `5` | How many identical attempts stop a subagent (it is warned two attempts earlier) |

Any key can also be set with an environment variable named `PROACTIVE_<KEY>`, which wins over the config file. An invalid value is logged and ignored.

## FAQ

**Does it cost extra tokens?**
It makes no model calls of its own; it only adds some text to the main agent's context. Waking the main agent to finish an interrupted report is one extra turn, about the same as you sending one more message.

**Will it change how cron jobs, the CLI or other platforms behave?**
No. Only the platforms in `governed_platforms` are affected. Cron jobs and every other platform run exactly as before.

**Does it replace Hermes' approvals or loop guard?**
No. They keep working as they do now. When Hermes' approval refuses a subagent's command, the plugin only tells the subagent not to work around it and to bring the question back.

## Compatibility

- Hermes Agent 0.22 or later (tested on v0.22.7).
- Python 3.10 or later, no runtime dependencies.
- CI runs `ruff check` and `pytest` on Python 3.10 through 3.14.

## Development

```bash
uv run --group dev pytest
uv run --group dev ruff check
```

`tests/test_hermes_e2e.py` drives the plugin through a real Hermes install when `hermes_cli` can be imported, and is skipped otherwise.

```
proactive-agent/      the plugin, and all that Hermes installs
├── secretary.py      turn budget, background detach, your messages mid-turn
├── handoff.py        brief checks, the briefing, working rules and result format
├── workers.py        running subagents, the repeat stop, notes on refused steps
├── reports.py        result notes, open questions, unreported results, restart recovery
├── ledger.py         the task ledger
├── prompt.py         the main agent's system prompt section
├── context.py        who is calling: main agent, subagent or cron
├── config.py         settings
├── util.py           helpers
├── __init__.py       hook registration
└── plugin.yaml       manifest
tests/                one file per module, plus end-to-end tests against Hermes
```

## For AI agents

[llms.txt](llms.txt) is a short index of this project for AI agents and answer engines, and [llms-full.txt](llms-full.txt) describes the whole project in one file.

## License

[MIT](LICENSE)
