# Hermes Proactive Agent

[![Release](https://img.shields.io/github/v/release/JerryLiu369/hermes-proactive-agent?label=release)](https://github.com/JerryLiu369/hermes-proactive-agent/releases)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![Hermes Plugin](https://img.shields.io/badge/Hermes-plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

[English](README.md) | 简体中文

[Hermes Agent](https://hermes-agent.nousresearch.com) 的插件，让聊天代理像一位带着团队的秘书一样工作。

你是老板。**主代理**是秘书：随时找得到，听懂你要什么，替你拍板，再向你汇报。**子代理**是工人：接手耗时长、步骤多、输出杂的活。插件负责让这套分工落到实处：聊天不卡住，工人一开始就拿到该拿的信息，做过的事不会被忘掉。

## 安装

```bash
hermes plugins install JerryLiu369/hermes-proactive-agent/proactive-agent
hermes plugins enable proactive-agent
```

Hermes 只会安装 `proactive-agent/` 这个目录。装好后重启网关。升级用 `hermes plugins update proactive-agent`。

建议：一个工人调用模型超过 60 次左右，通常已经卡住了。把 Hermes 的步数上限调低，它会更早交汇报。

```yaml
delegation:
  max_iterations: 60
```

## 工作原理

插件跟着一个任务走完全程，每个环节只做一件小事。

```
 你 ──► 秘书 ─────── delegate_task ───────► 工人
        1. 单轮预算       2. 交接            3. 工人限制
          ▲                                     │
          └──────────── 4. 结果与账本 ◄─────────┘
```

1. **单轮预算。** 秘书可以思考、可以调查，但每一轮有时间上限，长命令转到后台跑。你总能很快得到回复。
2. **交接。** 秘书了解整段对话，所以交接单由它来写。插件把太单薄的交接单退回去，并补上工人自己无从知道的内容。
3. **工人限制。** 工人反复做同一件事，就叫停，不让它把步数耗光。
4. **结果与账本。** 秘书知道每个工人怎么结束、该核实什么。持久化的账本记下每个任务，下一个工人从上一个停下的地方接着做，重启也不丢。

始终遵守四条原则：

- **不额外调用模型。** 所有检查都是普通代码。
- **Hermes 的事交给 Hermes。** 命令审批、过期文件保护、步数上限、卡死检测、完成通知，都由 Hermes 自己负责。
- **默认安静。** 不每轮注入内容，也不定时发消息。
- **绝不碍事。** 插件出错只记日志，不会拦住任何工具。

## 功能

### 1. 单轮预算

| 本轮已用时间 | 效果 |
|---|---|
| 60 秒以内 | 不干预 |
| 60 秒起 | 提醒一次：1 到 2 步内回复你，或者派活 |
| 120 秒起 | 暂停改文件和执行命令；读取、`delegate_task` 和收尾用的工具仍可用 |

- 你在一轮中途发来新消息，计时重新开始。
- 收尾用的工具是 `memory`、`todo`、`clarify`、`send_message`，以及只用于查看、轮询、读日志或结束后台任务的 `process_manage`。
- 前台命令超过 30 秒，通过 Hermes 自带的机制转到后台：进程继续跑，结束时 Hermes 会通知，秘书也会被告知命令没有失败。
- 拒绝 `process_manage(action='wait')`：等后台任务会让聊天再次卡住。

### 2. 交接

以下两种情况，`delegate_task` 每轮退回秘书一次：

- `context` 不足 120 个字符。退回时提示它补上：已知什么、具体目标、哪些不能动、什么时候停、什么算成功；
- 提到了另一个在跑的工人正在改的文件。秘书可以等它做完、用 steer 改它的方向，或者先停掉它。

第二次提交一定放行。放行的任务会在秘书自己写的 context 之后，附上一份简报：

| 内容 | 作用 |
|---|---|
| 你最近几条消息的原文 | 让"别动 X"这类约束传到工人手里 |
| `USER.md` 和 `MEMORY.md` | Hermes 不给子代理加载记忆 |
| 正在跑的其他工人和它们在改的文件 | 别去碰这些文件 |
| 这个对话最近没做完的任务 | 摘要、阻碍、收到的修正、改过的文件和日志路径，接着做而不是从头来 |
| 工作规则 | 只做份内的事、同一个错误出现两次就停、危险改动要能回退、核实真实结果 |
| 默认的 `output_schema` | `status`、`summary`、`evidence`、`blockers`；任务自带时不覆盖 |

### 3. 工人限制

工人用同一个工具、同样的参数，得到同样的结果：第 3 次提醒它换个办法，第 5 次叫停。叫停后它的工具调用一律拒绝，必须以 `blocked` 汇报，聊天里同时给你发一句提醒。这是插件唯一会主动发的消息。

### 4. 结果与任务账本

**结果。** 工人结束后，秘书下一轮开头会收到一条提示：工人给出的证据；或者提醒它声称成功却没有证据、没做完、被叫停了。秘书自己停掉的任务会注明是主动停的，不会被当成失败。

**账本。** 每个任务一条记录，由插件的钩子写入：

- 目标和状态；
- 改过的文件；
- 通过 `action='steer'` 发过的修正；
- 实时日志的路径；
- 结束时的摘要、证据和阻碍。

工作靠账本延续。Hermes 无法把结束的子代理再唤起来，所以同一个对话里的下一个工人会拿到没做完那个任务的记录。Hermes 中途重启时，秘书会被告知哪些任务丢了、副作用不确定、动过哪些文件。提示存在账本里，等到那个对话的下一轮才取出，重启也不会丢。

账本存放在 `<hermes home>/plugin-data/proactive-agent/ledger.db`，每个对话保留最近 20 个已结束的任务，最长 7 天。

### 生效范围

只管 `governed_platforms` 里的平台（默认 `["weixin"]`）及其派出的工人。其他平台、定时任务、别处派出的工人都不受影响。

## 配置

配置写在 `~/.hermes/config.yaml` 的 `plugins.entries.proactive-agent.settings` 下。每一项都可以用环境变量 `PROACTIVE_<配置名大写>` 覆盖。

```yaml
plugins:
  entries:
    proactive-agent:
      settings:
        governed_platforms: ["weixin"]
        turn_soft_seconds: 60
        turn_hard_seconds: 120
```

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `governed_platforms` | `["weixin"]` | 受管的平台，对秘书和它派出的工人生效 |
| `turn_soft_seconds` | `60` | 到这个秒数时，提醒秘书回复或派活 |
| `turn_hard_seconds` | `120` | 到这个秒数后，本轮暂停改文件和执行命令 |
| `terminal_auto_detach_seconds` | `30` | 前台命令超过这个秒数就转到后台（`0` 表示关闭） |
| `handoff_min_context_chars` | `120` | `context` 少于这个字符数时，每轮退回一次 |
| `worker_repeat_limit` | `5` | 同一步重复多少次就叫停工人（提前两次先提醒） |

## 兼容性

- Hermes Agent 0.22 及以上（已在 v0.22.7 上测试）。没有 `ctx.post_message` 时只是不发卡住提醒，其余功能照常。
- Python 3.10 及以上。

## 开发

```bash
uv run --group dev pytest
uv run --group dev ruff check
```

能导入 `hermes_cli` 时，`tests/test_hermes_e2e.py` 会通过真实安装的 Hermes 运行插件，否则自动跳过。

```
proactive-agent/      插件本体，也是 Hermes 唯一安装的目录
├── __init__.py       注册钩子
├── secretary.py      单轮预算、长命令转后台
├── handoff.py        delegate_task 检查与简报
├── workers.py        重复叫停、结果提示、卡住提醒
├── ledger.py         持久化任务账本
├── context.py        判断调用方：秘书、工人还是定时任务
├── prompt.py         系统提示词片段
├── config.py         配置
├── util.py           辅助函数
└── plugin.yaml       插件清单
tests/                每个模块一个测试文件，外加针对 Hermes 的端到端测试
```

## 许可证

[MIT](LICENSE)
