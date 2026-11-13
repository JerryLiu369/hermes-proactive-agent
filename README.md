# Hermes Super Agent

[![Release](https://img.shields.io/github/v/release/JerryLiu369/hermes-super-agent?label=release)](https://github.com/JerryLiu369/hermes-super-agent/releases)
[![CI](https://github.com/JerryLiu369/hermes-super-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-super-agent/actions/workflows/ci.yml)
[![Hermes Plugin](https://img.shields.io/badge/Hermes-plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

English | [简体中文](README_zh.md)

Hermes Super Agent coordinates [Hermes Agent](https://hermes-agent.nousresearch.com) chat sessions using a **Lead–Worker Super Agent** architecture.

Instead of allowing a single chat agent to block user conversations during protracted execution or dispatch unguided subagents that loop and drop findings, this plugin establishes an explicit separation of responsibilities:

- **Lead Agent (Main Session):** Remains continuously reachable in the chat channel, interprets user intent, formulates plans, investigates initial facts, resolves or escalates open questions, and inspects deliverables before presenting them.
- **Worker Subagents (Delegated Tasks):** Run in the background to execute heavy, noisy, or multi-step workloads (builds, test suites, exploratory web search, file refactoring). Workers receive comprehensive handoff briefings (conversation context, memory, peer activity, and operational boundaries), are constrained against tool-calling loops and unauthorized assumptions, and persist progress in an independent task ledger.

The plugin operates as deterministic Python hooks and middleware. It performs zero model inferences of its own, consumes zero extra tokens during inspection passes, and never injects unmediated messages into the user conversation. All communication with the user is driven solely by the Lead Agent.

---

## Installation

Install and enable the plugin via the Hermes CLI:

```bash
hermes plugins install JerryLiu369/hermes-super-agent/super-agent
hermes plugins enable super-agent
```

Recommended host settings in `~/.hermes/config.yaml` (`delegation.max_iterations: 90` to bound worker subagent steps, restart the Hermes gateway after updating):

```yaml
delegation:
  max_iterations: 90                # recommended iteration ceiling for worker subagents
plugins:
  entries:
    super-agent:
      allow_gateway_injection: true # permits waking the Lead Agent to deliver interrupted reports
```

Setting `allow_gateway_injection: true` allows the plugin to wake the Lead Agent via Hermes' internal gateway injection queue when an incoming background report was eclipsed by a concurrent user message. If omitted, unreported deliverables are held until the user's subsequent turn.

---

## Upgrading from `hermes-proactive-agent` (0.1.x)

Hermes Super Agent v0.2.0 is the direct successor to `hermes-proactive-agent`. To upgrade an existing deployment:

1. **Install and enable the new plugin:**
   ```bash
   hermes plugins install JerryLiu369/hermes-super-agent/super-agent
   hermes plugins enable super-agent
   # Optional: disable or uninstall the previous version
   hermes plugins disable proactive-agent
   ```
2. **Update configuration entries in `~/.hermes/config.yaml`:**
   Rename the configuration block from `plugins.entries.proactive-agent` to `plugins.entries.super-agent`:
   ```yaml
   plugins:
     entries:
       super-agent:
         allow_gateway_injection: true
         settings:
           governed_platforms: ["weixin"]
   ```
3. **Optional task ledger migration:**
   To preserve historical task records from v0.1.x:
   ```bash
   mkdir -p ~/.hermes/plugin-data/super-agent
   cp ~/.hermes/plugin-data/proactive-agent/ledger.db ~/.hermes/plugin-data/super-agent/ledger.db
   ```
4. **Environment variable compatibility:**
   The plugin recognizes `SUPER_AGENT_<KEY>` as the primary environment override while retaining full backward-compatible fallback for `PROACTIVE_<KEY>`.

---

## Architectural Challenges Addressed

Standard subagent delegation in interactive messaging channels exhibits six failure modes:

1. **Channel Unresponsiveness:** A main agent initiates a heavy compilation, multi-step code refactoring, or diagnostic script directly within the chat turn, freezing channel replies for tens of minutes.
2. **Context-Blind Subagents:** Dispatched subagents lack chat history and memory. They operate unaware of user constraints (for example, forbidden files), previous failed attempts, or specific user requirements.
3. **Repetitive Tool Loops and Speculative Failures:** Trapped subagents repeatedly execute failing tool invocations until exhaustion, or encounter critical bifurcations and make unauthorized architectural decisions rather than escalating.
4. **Unverified Completion Assertions:** Subagents report completion without verifiable artifacts, and the main agent echoes these claims to the user without inspection.
5. **Report Omission via Race Conditions:** A background subagent finishes while the user is actively submitting a message. The main agent replies to the new user input and silently drops the background deliverable.
6. **State Oblivion Across Restarts:** When subagents finish or when the gateway restarts mid-execution, records of touched files, steering directives, and partial outputs disappear.

---

## Core Mechanisms

### 1. Lead Agent Responsiveness

Each turn of the Lead Agent operates under deterministic time governance:

- **60-Second Soft Budget (`turn_soft_seconds: 60`):** The Lead Agent receives an operational reminder to synthesize an intermediate response or hand remaining execution to `delegate_task` within 1 to 2 steps.
- **120-Second Hard Pause (`turn_hard_seconds: 120`):** Mutating tools (file editing, file writing, terminal execution) are suspended for the remainder of the turn. Read-only and governance tools remain accessible: file reading, image inspection (`vision_analyze`), search tools, `delegate_task`, `memory`, `todo_list`, `clarify`, skill tools, and process management (`process_manage` actions `list`, `poll`, `log`, `kill`).
- **Mid-Turn User Input Reset:** When user messages arrive mid-turn, the turn timer resets to afford the Lead Agent adequate processing time.
- **Compression Isolation:** Hermes conversation compression pauses the turn timer. Long summarization runs do not consume the turn budget.
- **30-Second Terminal Auto-Detaching (`terminal_auto_detach_seconds: 30`):** Foreground terminal commands running past 30 seconds are automatically detached into background processes via Hermes' native yield mechanism (supported on the local terminal backend). Hermes tracks the PID and notifies the Lead Agent upon exit. Blocking on background tasks via `process_manage(action='wait')` is rejected to prevent deadlock.
- **Discipline Prompt Section:** Registers `super-agent.discipline` in the system prompt for governed sessions, defining Lead Agent boundaries and delegation behavior.

### 2. Worker Subagent Briefing and Context Handoff

Subagents do not inherit main session history. The plugin enforces briefing quality and injects vital background context prior to worker initialization:

- **Context Quality Gating:** The plugin validates `delegate_task` arguments. A brief shorter than 120 characters (`handoff_min_context_chars: 120`) is returned once per turn with a checklist of required parameters (known facts, exact target paths/URLs, invariant constraints, termination criteria, verification requirements). A repeated submission in the same turn is passed through.
- **File Contention Guard:** If a dispatch targets a file currently being modified by another active subagent, the call is returned once per turn to prevent write collisions.
- **Comprehensive Context Assembly:** Accepted tasks automatically receive an appended briefing block containing:
  - The user's last 6 messages verbatim, preserving immediate negative constraints and instructions.
  - Complete contents of `USER.md` and `MEMORY.md` (which Hermes omits during subagent dispatch).
  - Registry of concurrently active worker tasks and their assigned target files.
  - Records of recent unfinished tasks from the current session (prior findings, blockers, pending decisions, steer directives, modified files, transcript paths).
  - Explicit operational directives: strict scope adhesion, halting after two identical failures, escalating decisions instead of guessing, mandatory rollback paths for high-risk operations, and mandatory artifact verification.
  - Standardized JSON reporting schema.

### 3. Loop Guard and Decision Escalation

- **Normalized Outcome Tracking:** The plugin tracks every tool invocation across the worker's full lifespan (not merely consecutive steps). Return outputs are compared after masking variable IDs, timestamps, and memory addresses.
- **Loop Termination (`worker_repeat_limit: 5`):**
  - **3rd Identical Outcome:** Worker receives an explicit warning to alter strategy or report the obstacle.
  - **5th Identical Outcome:** Worker execution is terminated. Subsequent tool invocations are blocked, forcing the worker to conclude with status `blocked`. Overall step ceilings remain bounded by Hermes' `delegation.max_iterations` (recommended: 90).
- **Decision Escalation (`needs_decision`):** Subagents cannot query the user directly because Hermes does not expose `clarify` to subagents. When encountering unresolvable ambiguities (competing architectures, production impacts, irreversible data operations, financial costs) or when a tool call is denied by command approvals or security hooks:
  - The worker halts immediately and returns status `needs_decision`, accompanied by a structured `question` and discrete `options`.
  - When blocked by command approval or security hooks, an immediate directive instructs the worker not to search for bypasses.
  - The Lead Agent receives the escalation. If conversation history or accessible facts resolve the question, the Lead Agent decides autonomously; otherwise, it presents a single concise decision prompt to the user and subsequently launches a follow-up worker with the user's choice.

### 4. Deliverable Verification, Guaranteed Delivery, and Task Ledger

- **Deliverable Verification:** When a worker concludes, the Lead Agent's subsequent turn opens with a verification directive:
  - If status is `completed`, reported evidence is presented with instructions to spot-check reality before answering the user.
  - If completion lacks concrete proof, or if the worker halted due to repetition, explicit warnings are injected.
- **Lossless Report Delivery:** If a user submits a message concurrently with a background worker return:
  - The deliverable is flagged as `unreported`.
  - If the Lead Agent invokes a tool during the combined turn, the tool result appends a mandatory reminder to deliver the background finding alongside the user response.
  - If the Lead Agent answers without invoking tools, the plugin queues a single wake-up prompt via `ctx.inject_message` (up to 2 wake attempts per task) allowing the agent to provide the deliverable or emit `[SILENT]`.
  - If injection is disabled or interrupted, the reminder is attached to the user's subsequent incoming message. Results older than 24 hours are pruned.
- **Persistent SQLite Task Ledger:** All task lifecycles are persisted to `<hermes home>/plugin-data/super-agent/ledger.db`. The ledger tracks task ID, parent session, child session, goal, status (`running`, `completed`, `partial`, `blocked`, `needs_decision`, `failed`, `lost`, `stopped`), modified files, steer inputs, transcript locations, and reporting state.
- **Gateway Crash Recovery:** Upon gateway restart, orphaned running tasks are marked as `lost`. The Lead Agent is notified of disrupted tasks and touched files on its next turn to prevent unverified assumptions. The ledger retains 20 finished tasks per session for 7 days.

---

## Design Principles

- **Lead Agent Mediation:** The plugin communicates exclusively with the Lead Agent through injected context notes and gateway message injection. It never writes directly into user channels.
- **Deterministic and Event-Driven:** No polling intervals or periodic background heartbeats. Governance runs strictly in response to platform events (turn initialization, tool invocation, tool return, worker lifecycle events).
- **Zero Model Calls:** Inspection, gating, repeat detection, and handoff assembly run purely in Python without LLM evaluation or token expenditure.
- **Host Separation of Concerns:** Hermes retains native ownership over command approvals, workspace sandboxing, overall iteration limits, and tool execution. The plugin governs orchestration dynamics.
- **Standard Extension Contracts:** Implemented entirely via Hermes' documented hooks (`pre_llm_call`, `pre_api_request`, `pre_auxiliary_call`, `post_auxiliary_call`, `pre_tool_call`, `post_tool_call`, `transform_tool_result`, `subagent_start`, `subagent_stop`, `on_session_end`, `on_session_reset`, `on_session_finalize`) and `tool_execution` middleware.
- **Fail-Open Isolation:** All hook logic is wrapped in defensive exception handlers. Internal exceptions are logged and tool operations proceed unhindered.
- **Strict Scope Boundaries:** Governance applies only to platforms declared in `governed_platforms` (default: `["weixin"]`) and their child subagents. CLI sessions, cron schedules, and unlisted gateways run unmodified.

---

## Configuration Reference

Configuration options reside under `plugins.entries.super-agent.settings` in `~/.hermes/config.yaml`:

```yaml
plugins:
  entries:
    super-agent:
      allow_gateway_injection: true   # Hermes host capability
      settings:
        governed_platforms: ["weixin"]
        turn_soft_seconds: 60
        turn_hard_seconds: 120
        terminal_auto_detach_seconds: 30
        handoff_min_context_chars: 120
        worker_repeat_limit: 5
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `governed_platforms` | list | `["weixin"]` | Platform identifiers subject to Lead Agent and Worker Subagent governance |
| `turn_soft_seconds` | number | `60.0` | Elapsed seconds in a turn before reminding the Lead Agent to reply or delegate |
| `turn_hard_seconds` | number | `120.0` | Elapsed seconds in a turn before pausing mutating file and terminal tools |
| `terminal_auto_detach_seconds` | integer | `30` | Execution seconds before foreground terminal commands yield to background (`0` disables) |
| `handoff_min_context_chars` | integer | `120` | Minimum `context` character length in `delegate_task` required to pass initial gating |
| `worker_repeat_limit` | integer | `5` | Identical tool outcome count before terminating a looping subagent (warns at 3) |

### Environment Variable Overrides

Any setting can be overridden via environment variables. `SUPER_AGENT_<KEY>` takes precedence, followed by legacy `PROACTIVE_<KEY>` variables, then `config.yaml`, and finally internal defaults:

```bash
export SUPER_AGENT_TURN_SOFT_SECONDS=45
export SUPER_AGENT_WORKER_REPEAT_LIMIT=4
```

---

## Frequently Asked Questions

**Does the plugin consume additional LLM tokens?**  
No. The plugin performs zero model evaluations. It injects compact plain-text context notes into Lead Agent turns and appends briefings to `delegate_task`. An injected wake turn to report an interrupted deliverable consumes the standard tokens of a single short reply.

**Are CLI sessions, automated cron tasks, or alternate channels affected?**  
No. Platforms omitted from `governed_platforms` run entirely without intervention. CLI usage and scheduled tasks remain unconstrained.

**Does this replace native Hermes approvals or loop detection?**  
No. Hermes command approvals, permission barriers, and iteration ceilings function as normal. When an approval denies a subagent command, the plugin prevents evasion attempts and structures an escalation to the Lead Agent.

**How does the plugin prevent chat freezes during heavy tasks?**  
At 60 seconds, the Lead Agent is reminded to delegate. At 120 seconds, editing and shell execution are paused while delegation tools remain active. Foreground commands exceeding 30 seconds are automatically moved to the background, and `process_manage(action='wait')` is rejected.

**Why do native subagents operate without context, and how is this fixed?**  
Hermes initializes subagents in isolated sessions without chat history, user profiles, or memory files. The plugin intercepts `delegate_task`, ensures the brief meets minimum structural standards, and appends the user's latest 6 messages, full `USER.md` and `MEMORY.md`, running peer activities, and operational guidelines.

**How are subagent decisions and permission blocks handled?**  
Subagents lack direct user-query tools like `clarify`. When a worker encounters an ambiguous architectural choice or a blocked permission, it halts with status `needs_decision` and structured options. The Lead Agent resolves the issue from broader context or queries the user directly before dispatching a follow-up worker.

**What happens when a user message arrives simultaneously with a background result?**  
Hermes merges the incoming message into the reporting turn, which frequently causes the agent to ignore the background deliverable. The plugin marks the result as `unreported`, reminds the Lead Agent via tool results, or wakes the agent via message injection to ensure delivery.

---

## Repository Structure

```text
super-agent/          Installed plugin package (directory recognized by Hermes)
├── secretary.py      Turn budgets, 30s background detachment, mid-turn message tracking
├── handoff.py        Brief validation, handoff briefing assembly, operational directives
├── workers.py        Worker registry, tool repeat detection and termination, refusal guidance
├── reports.py        Deliverable verification, decision escalation, unreported state machine
├── ledger.py         SQLite task ledger and persistent note queue
├── prompt.py         Lead Agent system prompt section (super-agent.discipline)
├── context.py        Caller classification (governed Lead Agent, Worker subagent, cron)
├── config.py         Configuration parsing and environment variable overrides
├── util.py           Text processing, JSON serialization, and call signature utilities
├── __init__.py       Hook and middleware registration
└── plugin.yaml       Plugin manifest and configuration schema
tests/                Unit and end-to-end test suites
```

---

## Compatibility and Development

- **Hermes Agent:** Version 0.22 or higher (verified on v0.22.7).
- **Process Isolation:** Terminal background yielding requires default `plugins.isolation: in_process`. With `host` isolation, commands remain in the foreground.
- **Python:** 3.10 or higher with zero external runtime dependencies.
- **Continuous Integration:** Validated across Python 3.10 through 3.14 using `ruff check` and `pytest`.

To run test suites locally:

```bash
uv run --group dev pytest
uv run --group dev ruff check
```

---

## Machine-Readable Reference

For AI coding agents and automated documentation tools:
- [llms.txt](llms.txt): Compact, high-signal architectural summary and technical reference.

---

## License

[MIT](LICENSE)
