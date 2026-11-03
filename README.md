# Hermes Proactive Agent Governor (`proactive-agent`)

[![Hermes Plugin](https://img.shields.io/badge/Hermes-Plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Version](https://img.shields.io/badge/version-2.1.0-green.svg)](https://github.com/JerryLiu369/hermes-proactive-agent)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)

[English](#english) | [中文说明](#中文说明)

---

<a name="english"></a>
## English

**Hermes Proactive Agent Governor** (`proactive-agent`) is an execution governance plugin for [Hermes Agent](https://hermes-agent.nousresearch.com). It enforces autonomous agent discipline, eliminates blocking foreground commands, prevents infinite serial tool loops, and provides resilient self-healing when humans intervene mid-turn.

### 🎯 Core Philosophy: "All Heavy Tasks Asynchronous" (重任务通通异步)
Autonomous LLM agents easily fall into traps: freezing chat for minutes on long bash commands, running in endless monolithic trial-and-error loops within a single turn, or ignoring mid-flight user steering.

`proactive-agent` turns your agent into a disciplined, proactive orchestrator:
1. **Reply first, then delegate**: Provide immediate feedback to keep conversations snappy.
2. **Embrace background execution & delegation**: Long-running jobs run detached; multi-step goals fan out to subagents via `delegate_task`.
3. **Quiet execution**: No spamming PIDs or raw terminal logs into user chat.
4. **Verify before claiming done**: Check physical artifacts on disk before claiming success.

---

### ⚡ The Three Core Agent Pain Points Solved

#### 1. Adaptive Terminal Detach (>30s) — Never Freeze Foreground Chat
- **The Problem**: When an agent runs build scripts (`cargo build`, `npm install`), test suites, or training processes in the foreground, the terminal blocks. Chat freezes for minutes, risking gateway timeouts or thread drops.
- **The Solution**: 
  - Non-invasively monitors `BaseEnvironment._wait_for_process` without breaking native interrupt wiring.
  - If a foreground command exceeds `terminal_auto_detach_seconds` (default: `30s`), it triggers Hermes's native `yield_handler` to adopt the running process into `process_registry` with zero kill and `notify_on_complete=True`.
  - Replaces tool result notes via `transform_tool_result` to instruct the LLM: *"Command running asynchronously in the background. Conclude your turn immediately and release the chat!"*

#### 2. Turn Circuit Breaker (>5 rounds / >100s) — Break Monolithic Death Loops
- **The Problem**: Single-threaded agents often get stuck in endless trial-and-error loops (10+ serial bash/patch tool calls in a single response turn), burning tokens, degrading context quality, and locking the UI.
- **The Solution**:
  - **Batch-Level Round Counting**: Parallel tool calls emitted within a single assistant step share one round counter (`pre_api_request`), preventing premature tripping.
  - **Hard Circuit Breaker**: If serial rounds exceed `turn_max_tool_rounds` (default: `5`) or turn duration exceeds `turn_max_duration_seconds` (default: `100s`), heavy monolithic tools (`terminal`, `patch`, `write_file`, `execute_code`) are frozen.
  - **Meta-Tool Whitelist**: Lightweight meta-tools remain completely accessible:
    - `delegate_task`: Fan out heavy sub-tasks to isolated subagents in parallel.
    - `skill_manage` / `memory`: Perform atomic self-evolution and retain learnings without action distortion.
    - `skill_view` / `skills_list`: Read existing guidelines.
  - **Subagent Bypass Guard**: Child subagents (`task_id.startswith("sa-")`) bypass the breaker to execute subtasks cleanly.

#### 3. Human Intervention & Steer Self-Healing — Dynamic Quota Reset
- **The Problem**: Static quota systems freeze agents permanently when users jump in mid-flight to correct direction.
- **The Solution**:
  - Monitors `display_kind == "steer"` and `[OUT-OF-BAND USER MESSAGE]` markers in `pre_api_request` and `pre_llm_call`.
  - The moment a human intervenes (either in a new turn or out-of-band mid-turn), round counters and elapsed timers are instantly reset to zero. The agent is granted a fresh quota to execute the user's updated directive.
  - Automatically prunes stale turn states to guarantee zero memory leaks across sessions.

#### 4. Dynamic Prompt Discipline
- Dynamically renders and registers prompt context (`proactive-agent.discipline`) matching the active configuration into the agent's system prompt.

---

### ⚙️ Configuration Reference

Parameters can be configured in your Hermes profile config (`config.yaml`), plugin settings, or environment variables:

| Config Key | Env Var | Type | Default | Description |
|---|---|---|---|---|
| `terminal_auto_detach_seconds` | `PROACTIVE_TERMINAL_AUTO_DETACH_SECONDS` | `int` | `30` | Max seconds a foreground terminal command can run before being detached into the background. |
| `turn_max_tool_rounds` | `PROACTIVE_TURN_MAX_TOOL_ROUNDS` | `int` | `5` | Max serial tool rounds in the main agent turn before circuit breaker restricts execution. |
| `turn_max_duration_seconds` | `PROACTIVE_TURN_MAX_DURATION_SECONDS` | `int` | `100` | Max cumulative tool execution duration (seconds) in the main agent turn before circuit breaker trips. |

#### Example `config.yaml`
```yaml
plugins:
  entries:
    proactive-agent:
      enabled: true
      config:
        terminal_auto_detach_seconds: 30
        turn_max_tool_rounds: 5
        turn_max_duration_seconds: 100
```

---

### 🚀 Installation & Usage

#### Install via Hermes CLI
```bash
# Install plugin directly from GitHub
hermes plugins install github:JerryLiu369/hermes-proactive-agent

# Enable plugin
hermes plugins enable proactive-agent

# Verify plugin status
hermes plugins list
```

#### Uninstall
```bash
hermes plugins disable proactive-agent
hermes plugins uninstall proactive-agent
```

---

<a name="中文说明"></a>
## 中文说明

**Hermes Proactive Agent Governor** (`proactive-agent`) 是专为 [Hermes Agent](https://hermes-agent.nousresearch.com) 设计的执行治理与自我约束插件。它从根源杜绝前台长命令卡死终端、打破单轮大模型硬跑工具的死循环，并在用户中途插话时提供精准容错自愈能力。

### 🎯 核心治理哲学：重任务通通异步 (All Heavy Tasks Asynchronous)
自主大模型 Agent 在日常任务中最容易出现以下失控行为：
- 执行编译、拉取镜像、微调等耗时任务时一直卡在前台等待，导致与用户的聊天窗口彻底卡死；
- 遇到报错时在单轮内陷入盲目的“单体工具连环试错”（接连执行 10~15 轮 `terminal` / `patch`），烧光上下文并最终超时；
- 用户中途发消息纠偏，Agent 却受死板配额限制拒绝执行。

`proactive-agent` 树立四大执行支柱：
1. **先回执后委派 (Reply first, then delegate)**：遇到复杂非平凡任务，首轮简短回执用户并释放交互，绝不在主线程单体硬跑或 sleep 等待。
2. **全面拥抱后台挂起与子代理委派 (Background execution & delegation)**：耗时任务主动或自适应挂后台；多步骤目标通过 `delegate_task` 并发分发给子代理。
3. **静默执行 (Quiet execution)**：不在对话中复读内部 PID、执行器 ID 或裸命令。
4. **物料物理核验 (Verify before claiming done)**：交付前核验磁盘物料与执行结果。

---

### ⚡ 解决三大 Agent 核心痛点与机制

#### 痛点一：前台长命令卡死终端与超时 (Adaptive Terminal Detach > 30s)
- **痛点分析**：大模型使用前台 `terminal` 运行长命令（如编译项目、训练脚本、大文件下载）时，终端会阻塞数分钟直至超时爆掉，期间用户无法在 IM/CLI 得到任何响应。如果直接粗暴重写 `terminal` 工具，又会破坏 Hermes 原生线程中断与消息接入协议。
- **治理机制**：
  - 非侵入式挂接底层 `BaseEnvironment._wait_for_process`，无缝接入原生 `yield_handler`。
  - 一旦前台命令执行超过设定阈值（默认 `30s`），在**不杀进程**的前提下，将正在运行的 `Popen` 进程平滑过户至后台 `process_registry`，标记完成自动推送通知（`notify_on_complete=True`）。
  - 自动通过 `transform_tool_result` 注入指引，明确告知模型：“该命令已转入后台异步运行，切勿 polling / sleep-wait，立即向用户回复并释放对话轮次！”

#### 痛点二：单轮连续硬跑单体工具死循环 (Turn Circuit Breaker > 5 轮 / > 100s)
- **痛点分析**：Agent 在面对难以解决的代码报错时，容易陷入“修改 -> 报错 -> 修改 -> 报错”的单体死循环。
- **治理机制**：
  - **并发工具合并计轮**：现代 LLM 单次回复常并发吐出 2~4 个工具调用，插件在 `pre_api_request` 维度计轮，确保一次模型思考只算一轮，绝不误触熔断。
  - **硬熔断限制**：当主轮次工具调用达到第 6 轮（`> 5` 轮），或累计工具执行耗时达到 `100s` 时，立刻熔断重型单体执行工具（`terminal`, `patch`, `write_file`, `execute_code`）。
  - **元认知与自进化工具白名单放行**：
    - `delegate_task`：强制要求模型拆解任务，分发给隔离子代理并行处理；
    - `skill_manage` / `memory`：允许模型在熔断状态下沉淀经验与教训，杜绝“为存经验不得不派子代理”的动作形变；
    - `skill_view` / `skills_list`：允许检索规则。
  - **子代理隔离放行**：子代理（`task_id.startswith("sa-")`）拥有独立的沙箱生命周期，自动绕过熔断，避免任务死锁。

#### 痛点三：用户中途介入插话容错自愈 (Human Intervention & Steer Self-Healing)
- **痛点分析**：若熔断配额过于死板，当用户在单轮中途发送纠偏消息（Steer Message）时，Agent 会因为配额耗尽而直接拒止。
- **治理机制**：
  - 精准嗅探消息列表中的 `display_kind == "steer"` 及 `[OUT-OF-BAND USER MESSAGE]` 标记。
  - 一旦检测到人类中途介入或开启新轮次，立即原子级重置本轮 round_count 与耗时计数器，给予 Agent 全新配额以遵从用户的最新指示。
  - 会话重置（`on_session_reset`）、会话结束（`on_session_end`）与中断（`agent_loop_stopped`）时自动清理状态，杜绝内存泄漏。

#### 动态系统提示词注入
- 自动根据当前生效的阈值参数，将行为规范（`proactive-agent.discipline`）动态编译并注入到 Hermes 系统提示词中，从思维源头校准模型行为。

---

### ⚙️ 参数配置说明

可在 Hermes 配置文件 `config.yaml` 或系统环境变量中自定义参数：

| 配置项 | 对应环境变量 | 类型 | 默认值 | 作用说明 |
|---|---|---|---|---|
| `terminal_auto_detach_seconds` | `PROACTIVE_TERMINAL_AUTO_DETACH_SECONDS` | `int` | `30` | 前台命令最大允许执行秒数，超过后自动安全挂起至后台运行 |
| `turn_max_tool_rounds` | `PROACTIVE_TURN_MAX_TOOL_ROUNDS` | `int` | `5` | 主代理单轮最大串行工具调用轮数，超过后触发重任务熔断 |
| `turn_max_duration_seconds` | `PROACTIVE_TURN_MAX_DURATION_SECONDS` | `int` | `100` | 主代理单轮工具执行累计最大秒数，超过后触发熔断 |

---

### 🚀 安装与使用

#### 安装
```bash
# 从 GitHub 仓库一键安装
hermes plugins install github:JerryLiu369/hermes-proactive-agent

# 启用插件
hermes plugins enable proactive-agent

# 检查已安装插件列表与状态
hermes plugins list
```

#### 卸载
```bash
hermes plugins disable proactive-agent
hermes plugins uninstall proactive-agent
```

---

### 🛠️ 环境兼容性
- **Hermes Agent**: v0.8.0 及以上版本
- **Python**: 3.10+
- **操作系统**: Linux, macOS, WSL

---

### 📄 License

MIT License. Copyright (c) 2026 Jerry Liu.
