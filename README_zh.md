# Hermes Proactive Agent（`proactive-agent`）

[![Hermes Plugin](https://img.shields.io/badge/Hermes-Plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Version](https://img.shields.io/badge/version-0.1.9-green.svg)](https://github.com/JerryLiu369/hermes-proactive-agent)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)

[English](README.md) | [简体中文](README_zh.md)

---

`proactive-agent` 是 [Hermes Agent](https://hermes-agent.nousresearch.com) 的插件，让聊天代理像一位带着团队的私人秘书一样工作。

你是老板。**主代理**是秘书：随时能找到，理解你要什么，负责判断和拍板，最后向你汇报。**子代理**是工人：接手耗时长、步骤多、输出杂的活，开工时拿到完整的交接单，干活过程受到监督。

## 设计思路

插件跟着一个任务走完全程，在每个环节加一道规矩。

```
 你 ──► 秘书（主代理）──────── delegate_task ────────► 工人（子代理）
        1. 单轮预算              2. 交接单              3. 工人护栏
        长命令转后台             检查说明、附上背景      遇错快停、步数上限、
                                                       危险命令和覆盖写保护
          ▲                                                 │
          └─────────────── 4. 验收与进度 ◄──────────────────┘
```

所有规矩都出自三条原则：

- **秘书动脑，工人动手。** 秘书有足够的空间去调查和判断，但不会把聊天卡住好几分钟。慢活转到后台，或者交给工人。
- **交接单要给足信息。** 工人开工时没有记忆，也看不到聊天记录，所以每份交接单都要写清：已经知道什么、目标是什么、哪些不能动、怎样才算成功。插件负责检查交接单，并补上其余的背景。
- **工人受监督，结果要核实。** 工人卡住就尽早停下，只做交接单授权的事，不覆盖别人的成果。秘书在告诉你“办好了”之前，先核对证据。

## 功能

### 1. 单轮预算：让秘书随时能回话

主代理的每一轮对话都有时间和工具输出量的预算。

| 档位 | 默认触发条件（可配置） | 效果 |
|---|---|---|
| 绿区 | 时间和输出量都没超 | 不干预 |
| 黄区 | 本轮已过 60 秒，或工具输出累计 3 万字符 | 在下一个工具结果后附一次提醒：1 到 2 步内要么回复用户，要么派活 |
| 红区 | 120 秒，或累计 8 万字符 | 拒绝改文件、执行命令这类工具；只读工具和 `delegate_task` 仍可用 |

- 你在一轮中途发来新消息，预算会清零。
- 前台命令超过 30 秒，会通过 Hermes 自带的机制转到后台：进程继续运行，结束后 Hermes 会通知秘书。秘书会被告知命令没有失败，并看到目前已有的输出。
- 只会卡住聊天的操作会被拒绝：`process_manage(action='wait')`、对家目录、`/` 或 `~/.hermes` 的无边界 `grep -r` / `rg` / `find`，以及端口扫描。
- 插件在系统提示词里加了一小段，事先把这些规矩告诉秘书。

### 2. 交接单：让工人拿到全貌

每次调用 `delegate_task`，执行前都会先检查。

- **说明太简略，退回一次。** 如果 `context` 不足 120 个字符，或者任务涉及危险操作（SSH、防火墙、删除、虚拟机生命周期）却没写回退办法，插件每轮退回一次，并告诉秘书该补哪些内容。第二次提交一定放行，秘书不会被卡死。
- **自动附上背景。** 每个放行的任务都会附上：
  - 对话摘要：你要什么、已经查明了什么、试过什么、哪些不能动。摘要由辅助模型生成，失败时改用最近消息和工具结果的简单摘录；
  - 你的 `USER.md` 和 `MEMORY.md`（有长度上限）；
  - 工作规则：只做份内的事、遇错快停、不做无边界搜索、核实真实结果、做不完就上报卡点、用你的语言写汇报；
  - 默认的 `output_schema`（`status`、`summary`、`evidence`、`blockers`、`files_changed`），任务自带时不覆盖。
- 摘要走名为 `proactive_handoff_summary` 的辅助任务。默认跟随 `compression` 用的模型，也可以单独指定。

### 3. 工人护栏：盯住干活过程

只对受管会话派出的子代理生效。

- **遇错快停。** 同一个调用连续 3 次得到相同结果，或者同一种失败出现 3 次，就叫停这个工人：后续工具调用一律拒绝，要求它以 `blocked` 状态汇报具体卡在哪里。
- **步数上限。** 默认 40 轮工具调用，用到 80% 时提醒它收尾。
- **危险命令**（改 SSH 或防火墙、对 API 发 HTTP DELETE、对系统或家目录 `rm -rf`、删除云资源或终止实例、重启等）会被拒绝，除非交接单里写了回退办法。
- **禁止盲写覆盖。** 如果某个文件在工人开工后被别人改过，工人在重新读一遍之前，不能对它 `write_file` 或 `patch`。

### 4. 验收与进度：把事情收尾

- 工人交活后，秘书在下一轮会收到一条提示：列出工人给出的证据；如果工人声称成功却没有证据，或者是被插件叫停的，也会明确指出。
- 每一轮，秘书都能看到还有哪些工人在跑。
- 新任务和正在运行的工人涉及相同的路径、主机或域名时，插件会提醒秘书一次，先用 `delegate_task(action='stop')` 停掉旧工人，免得旧工人晚到的结果覆盖新成果。
- 工人运行期间，每 5 分钟往聊天里发一句进度；工人被叫停时立即发一条提醒。这需要运行在网关平台上，通过 `ctx.post_message` 发送，语言跟随你。

### 生效范围

只有 `governed_platforms` 里的平台会受影响，默认是 `["weixin"]`。其他平台、定时任务，以及由不受管会话派出的子代理，都不受影响。

## 配置

所有配置项写在 `~/.hermes/config.yaml` 的 `plugins.entries.proactive-agent.settings` 下。直接写在 `proactive-agent:` 下面的配置项会被 Hermes 忽略。每个配置项也可以用环境变量 `PROACTIVE_<配置名大写>` 覆盖。

| 配置项 | 默认值 | 含义 |
|---|---|---|
| `governed_platforms` | `["weixin"]` | 受管的平台，对其主代理和它派出的子代理生效 |
| `terminal_auto_detach_seconds` | `30` | 前台命令超过这个秒数就转到后台（0 表示关闭） |
| `turn_soft_seconds` | `60` | 进入黄区的秒数 |
| `turn_hard_seconds` | `120` | 进入红区的秒数 |
| `turn_soft_output_chars` | `30000` | 工具输出达到这个字符数也进入黄区 |
| `turn_hard_output_chars` | `80000` | 工具输出达到这个字符数也进入红区 |
| `handoff_min_context_chars` | `120` | `context` 少于这个字符数时退回一次 |
| `handoff_summary_enabled` | `true` | 是否用辅助模型生成对话摘要 |
| `handoff_summary_timeout_seconds` | `12` | 摘要调用的超时，要明显小于 `plugins.hook_callback_timeout`（30 秒） |
| `handoff_memory_max_chars` | `4000` | 每次交接附带的记忆上限（0 表示不附带） |
| `worker_max_tool_rounds` | `40` | 工人的步数上限 |
| `worker_repeat_limit` | `3` | 重复多少次就叫停工人 |
| `worker_guard_risky_commands` | `true` | 没写回退办法时，拒绝工人执行危险命令 |
| `worker_write_guard` | `true` | 禁止覆盖别人改过的文件 |
| `verify_child_results` | `true` | 工人交活后提醒秘书核实什么 |
| `progress_push_enabled` | `true` | 往聊天里推送进度和卡住提醒 |
| `progress_interval_seconds` | `300` | 两次进度推送之间的秒数 |

```yaml
plugins:
  entries:
    proactive-agent:
      settings:
        governed_platforms: ["weixin"]
        turn_soft_seconds: 60
        turn_hard_seconds: 120
        worker_max_tool_rounds: 40
```

建议把 `delegation.max_iterations` 设得比 `worker_max_tool_rounds` 大。这样插件的步数上限会先触发，让工人先交一份汇报，而不是直接撞上 Hermes 的硬上限。

## 安装

```bash
hermes plugins install github:JerryLiu369/hermes-proactive-agent
hermes plugins enable proactive-agent
```

手动安装：把仓库克隆到 `~/.hermes/plugins/proactive-agent`，然后运行 `hermes plugins enable proactive-agent`。

## 开发

```bash
pip install pytest pyyaml
pytest -q
```

仓库根目录就是插件包，测试以包的方式加载它（见 `tests/conftest.py`）。`tests/test_hermes_e2e.py` 在能导入 `hermes_cli` 时会针对真实的 Hermes 运行，否则自动跳过。

| 模块 | 作用 |
|---|---|
| `__init__.py` | 注册钩子 |
| `config.py` | 读取配置 |
| `context.py` | 判断调用方：受管主代理、工人还是定时任务 |
| `secretary.py` | 单轮预算、长命令转后台、对话快照 |
| `handoff.py` | `delegate_task` 交接单检查与背景附带 |
| `workers.py` | 工人登记、护栏、验收提醒、进度推送 |
| `guards.py` | 命令分类与文本工具函数 |
| `prompt.py` | 系统提示词片段 |

## 兼容性

- 需要 Hermes Agent 支持以下能力：`pre_tool_call` 改写参数、`subagent_start` / `subagent_stop`、`ctx.llm` 和 `ctx.post_message`。已在 Hermes v0.22.7 上测试。缺少可选能力时会安静降级：没有摘要模型就改用简单摘录；没有 `post_message` 就不推送进度。
- Python 3.10 及以上。

## License

[MIT](LICENSE)
