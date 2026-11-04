# Hermes Proactive Agent Governor (`proactive-agent`)

[![Hermes Plugin](https://img.shields.io/badge/Hermes-Plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Version](https://img.shields.io/badge/version-0.1.1-green.svg)](https://github.com/JerryLiu369/hermes-proactive-agent)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)

[English](README.md) | [简体中文](README_zh.md)

---

**Hermes Proactive Agent Governor** (`proactive-agent`) is an execution governance plugin for [Hermes Agent](https://hermes-agent.nousresearch.com). It enforces proactive orchestrator discipline, eliminates blocking foreground commands, breaks infinite serial trial-and-error tool loops, and provides resilient self-healing when humans intervene mid-turn.

## 🎯 Core Philosophy: "All Heavy Tasks Asynchronous"

Autonomous LLM agents easily fall into common traps:
- **Foreground Freezes**: Executing long compilation, installation, or model scripts in the foreground, blocking chat UI and gateway connections.
- **Serial Trial-and-Error Loops**: Burning tokens and context length in endless monolithic loops (e.g. 10+ consecutive `terminal` or `patch` calls) within a single turn.
- **Quota Lockout on Steering**: Refusing to act after human intervention due to rigid, static execution limits.

`proactive-agent` transforms your agent into a disciplined, proactive orchestrator:
1. **Reply first, then delegate**: Acknowledge requests immediately in turn 1 and release the conversation. Never lock chat behind blocking sequential tool calls.
2. **Embrace background execution & delegation**: Long-running commands run detached in the background; complex multi-step goals fan out in parallel to subagents via `delegate_task`.
3. **Quiet execution**: Keep conversations clean without spamming internal PIDs or raw terminal logs into user chat.
4. **Verify before claiming done**: Check physical artifacts on disk before declaring completion.

---

## 🏛️ System Architecture & Workflow

```text
+----------------------------------------------------------------------------------------------------+
|                                    Hermes Proactive Agent Governor                                 |
+----------------------------------------------------------------------------------------------------+
|                                                                                                    |
|   +-----------------------+                                                                        |
|   | User Turn Initiation  |                                                                        |
|   +-----------+-----------+                                                                        |
|               |                                                                                    |
|               v                                                                                    |
|   +-----------------------+              Steer Message / Intervention                              |
|   | LLM Reasoning & Step  |<....................................................................+   |
|   +-----------+-----------+                                                                     :   |
|               |                                                                                 :   |
|       +-------+-------+                                                                         :   |
|       |               |                                                                         :   |
|       v (Terminal)    v (Tool Call)                                                             :   |
|  +---------+    +----------------------------+                                                  :   |
|  | Execute |    |   Circuit Breaker Check    |                                                  :   |
|  | Command |    |   - Round Count > 5?       |                                                  :   |
|  +----+----+    |   - Turn Elapsed > 90s?    |                                                  :   |
|       |         +--------------+-------------+                                                  :   |
|       |                        |                                                                :   |
|   [ > 30s? ]            +------+------+                                                         :   |
|    /     \              |             |                                                         :   |
|  Yes      No          Pass          Trip (>5 rds / >90s)                                        :   |
|   |        |            |             |                                                         :   |
|   v        v            v             v                                                         :   |
| [Detach] [Done]    [Execute]   [Freeze Heavy Tools]                                             :   |
|   |                     |             |                                                         :   |
|   |                     |             v                                                         :   |
|   |                     |     [Force delegate_task                                              :   |
|   |                     |      to Isolated Subagents]                                           :   |
|   |                     |             |                                                         :   |
|   +------------+--------+             |                                                         :   |
|                |                      |                                                         :   |
|                v                      v                                                         :   |
|       +------------------------------------+                                                    :   |
|       |  Atomic State Tracker & Governor   |                                                    :   |
|       |  (Dynamic Prompt & Quota Watcher)  |<...................................................+   |
|       +-----------------+------------------+                                                        |
|                         |                                                                           |
|                         v                                                                           |
|       +------------------------------------+                                                        |
|       |  Prompt Response & Fast Turn Hand  |                                                        |
|       +------------------------------------+                                                        |
+----------------------------------------------------------------------------------------------------+
```

---

## ⚡ Core Governance Capabilities

### 1. Adaptive Terminal Detach (>30s) — Never Freeze Foreground Chat
- **The Problem**: When an agent runs build scripts (`cargo build`, `npm install`), test suites, or training processes in the foreground, the terminal blocks. Chat freezes for minutes, risking gateway timeouts or connection drops.
- **The Solution**:
  - Non-invasively monitors `BaseEnvironment._wait_for_process` without breaking native interrupt wiring.
  - If a foreground command exceeds `terminal_auto_detach_seconds` (default: `30s`), it triggers Hermes's native `yield_handler` to adopt the running process into `process_registry` with zero kill and `notify_on_complete=True`.
  - Replaces tool result notes via `transform_tool_result` to instruct the LLM: *"Command running asynchronously in the background. Conclude your turn immediately and release the chat!"*
  - **Subagent & Cron Autonomous Bypass**: Subagents (`task_id.startswith("sa-")`, `platform="subagent"`) and scheduled Cron tasks (`platform="cron"`, `task_id.startswith("cron:")`, `HERMES_CRON_SESSION`) skip the 30s detach timer completely, allowing background workers and scheduled batch jobs to execute long-running commands without unwanted detachment.

### 2. Turn Circuit Breaker (>5 rounds / >90s) — Break Monolithic Death Loops
- **The Problem**: Single-threaded agents often get stuck in endless trial-and-error loops (10+ serial bash/patch tool calls in a single response turn), burning tokens, degrading context quality, and locking the UI.
- **The Solution**:
  - **Batch-Level Round Counting**: Parallel tool calls emitted within a single assistant step share one round counter (`pre_api_request`), preventing premature tripping.
  - **Hard Circuit Breaker**: If serial rounds exceed `turn_max_tool_rounds` (default: `5`) or turn duration exceeds `turn_max_duration_seconds` (default: `90s`), heavy monolithic tools (`terminal`, `patch`, `write_file`, `execute_code`) are frozen.
  - **Meta-Tool Whitelist**: Lightweight governance and meta-tools remain completely accessible:
    - `delegate_task`: Fan out heavy sub-tasks to isolated subagents in parallel.
    - `skill_manage` / `memory`: Perform atomic self-evolution and retain learnings without action distortion.
    - `skill_view` / `skills_list`: Read existing guidelines and documentation.
  - **Subagent & Cron Autonomous Bypass Guard**: Child subagents (`task_id.startswith("sa-")`) and unattended scheduled Cron tasks (`platform="cron"`, `task_id.startswith("cron:")`, `HERMES_CRON_SESSION`) bypass the breaker entirely to execute multi-step automated jobs cleanly without premature freezing.

### 3. Human Intervention & Steer Self-Healing — Dynamic Quota Reset
- **The Problem**: Static quota systems freeze agents permanently when users jump in mid-flight to correct direction.
- **The Solution**:
  - Monitors `display_kind == "steer"` and `[OUT-OF-BAND USER MESSAGE]` markers in `pre_api_request` and `pre_llm_call`.
  - The moment a human intervenes (either in a new turn or out-of-band mid-turn), round counters and elapsed timers are instantly reset to zero. The agent is granted a fresh quota to execute the user's updated directive.
  - Automatically prunes stale turn states to guarantee zero memory leaks across sessions.

### 4. Dynamic Prompt Discipline & Autonomous Zero-Injection
- Dynamically renders and registers prompt context (`proactive-agent.discipline`) matching the active configuration into the main agent's system prompt.
- **Subagent & Cron Zero-Injection**: When running in a subagent or scheduled cron session (`platform in ("subagent", "cron")`, `session_id.startswith("cron_")`, `HERMES_CRON_SESSION`), prompt generation returns an empty string, ensuring background workers and cron jobs have zero prompt pollution and zero token overhead.

```text
Role: Main dialogue orchestrator. Stay responsive; heavy execution never blocks the chat turn.

1. Triage & Dispatch-Then-Yield
- LIGHT (read files, status checks, quick probes): finish in <=5 tool rounds, then answer.
- HEAVY (multi-step edits, refactors, builds, test suites, training, commands likely >30s):
  1. Scope minimally and aim to dispatch as early as possible, but MUST dispatch before tool round 5 or 90s:
     - Complex reasoning or code tasks: fan out via delegate_task.
     - Long shell commands: run terminal(background=True, notify=True).
  2. Release turn immediately: output a concise one-line status to the user and end your turn. NEVER run sleep or poll.

2. Hardware Governance Limits (Main Thread Only)
- Foreground command >30s: auto-detached to background with completion notification. Do not wait; inform the user and end your turn.
- At tool round 6 (round >=6) or >90s turn time: terminal, patch, write_file and execution tools are frozen. Only delegate_task, memory, skill_manage, skill_view, skills_list remain. Do not retry; delegate remaining work or deliver current state.

3. Event Wakeup & Physical Verification
- Asynchronous tasks wake the conversation automatically upon completion.
- Verify physical on-disk artifacts and real exit codes before answering. Deliver verified results directly — never make the user ask "is it done?".
- Never expose internal PIDs, session_ids, or raw commands. Keep output clean.
```

---

## ⚙️ Configuration Reference

Parameters can be configured in your Hermes profile config (`config.yaml`), plugin settings, or environment variables:

| Config Key | Env Var | Type | Default | Description |
|---|---|---|---|---|
| `terminal_auto_detach_seconds` | `PROACTIVE_TERMINAL_AUTO_DETACH_SECONDS` | `int` | `30` | Max seconds a foreground terminal command can run before being detached into the background. |
| `turn_max_tool_rounds` | `PROACTIVE_TURN_MAX_TOOL_ROUNDS` | `int` | `5` | Max serial tool rounds in the main agent turn before circuit breaker restricts execution. |
| `turn_max_duration_seconds` | `PROACTIVE_TURN_MAX_DURATION_SECONDS` | `int` | `90` | Max cumulative tool execution duration (seconds) in the main agent turn before circuit breaker trips. |

### Example `config.yaml`
```yaml
plugins:
  entries:
    proactive-agent:
      enabled: true
      config:
        terminal_auto_detach_seconds: 30
        turn_max_tool_rounds: 5
        turn_max_duration_seconds: 90
```

---

## 🚀 Installation & Usage

### Install via Hermes CLI
```bash
# Install plugin directly from GitHub
hermes plugins install github:JerryLiu369/hermes-proactive-agent

# Enable plugin
hermes plugins enable proactive-agent

# Verify plugin status
hermes plugins list
```

### Manual Installation
```bash
git clone https://github.com/JerryLiu369/hermes-proactive-agent.git ~/.hermes/plugins/proactive-agent
hermes plugins enable proactive-agent
```

### Uninstall
```bash
hermes plugins disable proactive-agent
hermes plugins uninstall proactive-agent
```

---

## 🔍 Troubleshooting & FAQ

| Question / Symptom | Root Cause | Recommended Action |
|---|---|---|
| **Foreground command detached after 30s** | Adaptive timer reached 30s threshold. | The command is running safely in background. Let the agent wrap up its turn; completion notification will arrive automatically. |
| **Circuit breaker triggered (`[Circuit Breaker] Quota reached`)** | The agent executed >5 serial tool rounds or took >90s in a single turn. | The agent is prompted to delegate remaining work via `delegate_task` or deliver its current findings. |
| **Do subagents inherit circuit breaker or terminal detach limits?** | Subagents are scoped to subtasks. | No. Subagents (`task_id.startswith("sa-")`, `platform="subagent"`) have full bypass (no 30s detach timer, no circuit breaker) and zero prompt injection overhead. |
| **Does mid-turn user steering get blocked?** | Prior turns may have consumed the quota. | Any new user input or out-of-band message immediately resets turn counters and grants a fresh quota. |
| **How to run tests locally?** | Standard testing workflow. | Run `pytest -v` in the root of the repository. |

---

## 🛠️ Environment Compatibility

- **Hermes Agent**: v0.8.0 and above
- **Python**: 3.10, 3.11, 3.12+
- **OS**: Linux, macOS, WSL

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
