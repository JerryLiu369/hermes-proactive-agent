# Hermes Proactive Agent

[![Release](https://img.shields.io/github/v/release/JerryLiu369/hermes-proactive-agent?label=release)](https://github.com/JerryLiu369/hermes-proactive-agent/releases)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![Hermes Plugin](https://img.shields.io/badge/Hermes-plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

[English](README.md) | 简体中文

`proactive-agent` 是 [Hermes Agent](https://hermes-agent.nousresearch.com) 的插件，让聊天代理像一位带着团队的私人秘书一样工作。

你是老板。**主代理**是秘书：随时能找到，理解你要什么，负责判断和拍板，最后向你汇报。**子代理**是工人：接手耗时长、步骤多、输出杂的活。

- [安装](#安装)
- [工作原理](#工作原理)
- [功能](#功能)
- [配置](#配置)
- [开发](#开发)

## 安装

```bash
hermes plugins install JerryLiu369/hermes-proactive-agent/proactive-agent
hermes plugins enable proactive-agent
```

插件本体在仓库的 `proactive-agent/` 目录里，Hermes 只会安装这个目录。手动安装：把 `proactive-agent/` 复制到 `~/.hermes/plugins/proactive-agent`，然后运行 `hermes plugins enable proactive-agent`。

建议同时调低 Hermes 的工人步数上限。一个工人调用模型超过 60 次左右，通常已经卡住了；上限低一些，它会更早交汇报。

```yaml
delegation:
  max_iterations: 60
```

## 工作原理

插件跟着一个任务走完全程，每个环节只做一件简单的事。

```
 你 ──► 秘书（主代理）──────── delegate_task ────────► 工人（子代理）
        1. 单轮预算               2. 交接                3. 工人限制
          ▲                                                 │
          └────────────── 4. 结果与任务账本 ◄───────────────┘
```

1. **秘书随时能回话。** 它可以自己调查和思考，但每一轮有时间预算，长命令转到后台跑。
2. **交接单由秘书来写。** 秘书本来就了解整段对话，所以由它自己写交接单。插件只把明显太短的交接单退回一次，并补上工人无从知道的内容：你最近说的话、你的记忆和几条工作规则。
3. **工人不空转。** 反复做同一件事、得到同一个结果，就叫停。步数上限用 Hermes 自己的 `delegation.max_iterations`。
4. **事情有收尾。** 每个工人结束后，秘书会知道结果如何、该核实什么。任务账本记住每个任务，重启也不丢，下一个工人会从上一个的发现接着做。

Hermes 已经做了的安全检查，交给 Hermes：子代理执行危险命令默认会被拒绝（`delegation.subagent_auto_approve: false`），文件工具也会拒绝覆盖已经过期的文件。插件不会额外调用模型。

## 功能

### 1. 单轮预算

| 本轮已用时间 | 效果 |
|---|---|
| 60 秒以内 | 不干预 |
| 60 秒起 | 在下一个工具结果后附一次提醒：1 到 2 步内要么回复你，要么派活 |
| 120 秒起 | 暂停改文件、执行命令这类工具；读取类工具、`delegate_task` 和收尾用的工具（`memory`、`todo`、`clarify`、`send_message`，以及只用于查看、轮询、读日志或结束后台任务的 `process_manage`）仍可用 |

- 你在一轮中途发来新消息，预算会清零。
- 前台命令超过 30 秒，会通过 Hermes 自带的机制转到后台：进程继续运行，结束后 Hermes 会通知秘书。秘书会被告知命令没有失败。
- 拒绝 `process_manage(action='wait')`：等待后台任务会让聊天再次卡住。
- 插件在系统提示词里加了一小段，事先把这些规矩告诉秘书。

### 2. 交接

- `delegate_task` 的 `context` 不足 120 个字符时，每轮退回一次，并附一份简短清单：已知什么、具体目标、哪些不能动、什么时候停、什么算成功。第二次提交一定放行。
- `delegate_task` 提到了另一个在跑的工人正在改的文件时，也每轮退回一次：等它做完、用 steer 改它的方向，或者先停掉它。第二次提交一定放行。
- 每个放行的任务，在秘书自己写的 context 之后，会附上：
  - 你最近几条消息的原文，让“别动 X”这类约束原样传给工人；
  - 你的 `USER.md` 和 `MEMORY.md`，完整附上（Hermes 不会给子代理加载记忆，这两份文件的长度 Hermes 本身已有上限）；
  - 任务账本里的内容：正在并行跑的其他工人和它们在改的文件，以及这个对话里最近没做完的任务（摘要、阻碍、中途收到的修正、改过的文件、完整日志路径）；
  - 工作规则：只做份内的事、同一个错误出现两次就停、危险改动必须有回退办法、别动其他工人正在改的文件、核实真实结果；
  - 默认的 `output_schema`（`status`、`summary`、`evidence`、`blockers`），任务自带时不覆盖。

### 3. 工人限制

只对受管会话派出的子代理生效。

- **同一步、同一个结果。** 同一个工具、同样的参数、得到同样的结果：重复 3 次时，提醒工人换个办法；重复 5 次就叫停。叫停后，它后续的工具调用一律拒绝，要求它以 `blocked` 状态汇报。聊天里会给你发一条简短提醒。
- **步数上限。** 交给 Hermes 的 `delegation.max_iterations`。用完时，Hermes 会再给工人一次不能调用工具的机会写汇报，并告诉秘书任务被截断了。

### 4. 结果与任务账本

- 工人结束后，秘书在下一轮会收到一条提示：工人给出的证据；或者指出它声称成功却没有证据、没有做完、被插件叫停。
- 秘书需要时用 `delegate_task(action='list')` 查看在跑的工人；系统提示词会要求它停掉已经过时的工人（`action='stop'`）。插件不会每轮都注入工人列表，也不会定时发消息。
- 任务账本给每个任务记一条：目标、状态、改过的文件、通过 `action='steer'` 发过的修正、实时日志路径，以及结束时的摘要、证据和阻碍。账本只由插件的钩子写入，不调用模型，存放在 `<hermes home>/plugin-data/proactive-agent/ledger.db`，每个对话保留最近 20 个已结束的任务，最长 7 天。
- 秘书用 `action='stop'` 停掉的任务会立刻记为已停止：它不再算作在跑，但做了一半的内容仍会交给下一个工人；它的结束提示会说明这是秘书主动停的。
- 一条记录如果一直等不到结束，6 小时后就不再算作在跑，和内存里工人列表的过期时间一致。
- Hermes 不能把已经结束的子代理再唤起来，重启后也不会续跑。所以同一个对话里下一个工人会自动拿到没做完那个任务的记录。重启之后，秘书会被告知哪些任务当时还在跑、它们的副作用不确定、改过哪些文件。
- 结果提示和重启提示存在账本里，等到那个对话的下一轮才取出。所以重启，或者 CLI 进程先加载了插件，都不会让提示丢失。
- 插件自己往聊天里发的消息只有一种：工人因为反复做同一件事被叫停时的提醒（网关平台，通过 `ctx.post_message` 发送）。

### 生效范围

只有 `governed_platforms` 里的平台会受影响，默认是 `["weixin"]`。其他平台、定时任务，以及由不受管会话派出的子代理，都不受影响。插件自身出错只记日志，不会拦住任何工具。

## 配置

配置写在 `~/.hermes/config.yaml` 的 `plugins.entries.proactive-agent.settings` 下。直接写在 `proactive-agent:` 下面的配置项会被 Hermes 忽略。每个配置项也可以用环境变量 `PROACTIVE_<配置名大写>` 覆盖。

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
| `governed_platforms` | `["weixin"]` | 受管的平台，对其主代理和它派出的子代理生效 |
| `terminal_auto_detach_seconds` | `30` | 前台命令超过这个秒数就转到后台（0 表示关闭） |
| `turn_soft_seconds` | `60` | 到这个秒数时提醒秘书回复或派活 |
| `turn_hard_seconds` | `120` | 到这个秒数后，本轮暂停改文件和执行命令 |
| `handoff_min_context_chars` | `120` | `context` 少于这个字符数时，每轮退回一次 |
| `worker_repeat_limit` | `5` | 同一步得到同一结果达到这个次数就叫停工人（提前两次先提醒） |

## 开发

```bash
uv run --group dev pytest          # 或者：pip install --group dev && pytest
uv run --group dev ruff check
```

`tests/test_hermes_e2e.py` 在能导入 `hermes_cli` 时，会通过真实的 Hermes 加载插件运行，否则自动跳过。

```
.
├── proactive-agent/       插件本体（Hermes 安装的就是这个目录）
│   ├── plugin.yaml        插件清单：钩子和配置项
│   ├── __init__.py        入口：register() 注册钩子
│   ├── config.py          读取配置
│   ├── context.py         判断调用方：受管主代理、工人还是定时任务
│   ├── secretary.py       单轮预算、长命令转后台
│   ├── handoff.py         delegate_task 检查与附带内容
│   ├── workers.py         重复叫停、结果提示、卡住提醒
│   ├── ledger.py          持久化任务账本（plugin-data 下的 SQLite）
│   ├── prompt.py          系统提示词片段
│   └── util.py            文本和工具结果的辅助函数
├── tests/                 每个模块一个测试文件，外加针对 Hermes 的端到端测试
├── pyproject.toml         项目信息、开发依赖、ruff 和 pytest 配置
└── llms.txt               给语言模型看的项目摘要
```

## 兼容性

- Hermes Agent 0.22 及以上，需要支持 `pre_tool_call` 改写参数、`subagent_start` / `subagent_stop` 和 `ctx.post_message`（已在 v0.22.7 上测试）。没有 `post_message` 时，聊天里收不到卡住提醒。
- Python 3.10 及以上。

## 许可证

[MIT](LICENSE)
