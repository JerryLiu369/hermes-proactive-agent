# Hermes Proactive Agent

[![Release](https://img.shields.io/github/v/release/JerryLiu369/hermes-proactive-agent?label=release)](https://github.com/JerryLiu369/hermes-proactive-agent/releases)
[![CI](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JerryLiu369/hermes-proactive-agent/actions/workflows/ci.yml)
[![Hermes Plugin](https://img.shields.io/badge/Hermes-plugin-blue.svg)](https://hermes-agent.nousresearch.com)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

[English](README.md) | 简体中文

这是 [Hermes Agent](https://hermes-agent.nousresearch.com) 的一个插件，让聊天里的主代理像一位靠谱的秘书那样做事：慢活交给子代理去做，自己随时能找到；每件活做完，结果都要核实过、确实告诉了你、也记了下来。

它是为在聊天软件里使用 Hermes 准备的（默认是微信）。插件自己不调用任何模型，也从不往你的聊天里发消息，你听到的话始终出自主代理。

## 安装

```bash
hermes plugins install JerryLiu369/hermes-proactive-agent/proactive-agent
hermes plugins enable proactive-agent
```

建议再在 `~/.hermes/config.yaml` 里加两项设置（都是可选的），然后重启网关：

```yaml
delegation:
  max_iterations: 60                # 需要更多步数的子代理，多半已经卡住了
plugins:
  entries:
    proactive-agent:
      allow_gateway_injection: true # 允许插件唤醒主代理，把被你打断的汇报补上
```

不开 `allow_gateway_injection` 的话，被你打断的汇报会等到你发下一条消息时再补。如果你是用别的方式装的插件，把 `proactive-agent` 换成 `hermes plugins list` 里显示的 id。

## 为什么要做这个

Hermes 本来就能把活交给在后台运行的子代理。可一旦在聊天里用起来，总有几件事会出岔子：

- 主代理自己跑起了构建，或者一头扎进长时间的排查，聊天里十分钟没有回音。
- 子代理出发时什么都不知道：你说过什么、之前试过什么、哪些东西不能碰。
- 卡住的子代理一遍遍跑同一条命令，直到步数用完。或者碰到一个不该由它来做的选择，干脆自己猜一个。
- 所谓"做完了"，只是子代理自己这么说。
- 结果刚回来，你正好发了条消息。主代理回了你这条，那个结果就再也没人提。
- 子代理一结束，或者 Hermes 一重启，它做过什么就没了。

这个插件逐一处理这些问题，下面按顺序说明。

## 它做了什么

### 聊天不会卡住

主代理的每一轮都有时间预算。到 60 秒时，它会收到一次提醒：一两步之内回复你，或者把剩下的活交给子代理。到 120 秒时，这一轮剩下的时间里不能再改文件、执行命令，但还能读取、派活和收尾：`memory`、技能相关的工具、`todo_list`、`clarify`，以及用 `process_manage` 查看、轮询、读日志或结束后台任务。你在一轮中途发来消息，计时就重新开始。

长命令也不会占住这一轮。终端命令跑了 30 秒还没结束，就通过 Hermes 自带的让出机制转到后台：进程继续跑，结束时 Hermes 会通知主代理，主代理也会被告知这条命令并没有失败。用 `process_manage(action='wait')` 干等后台任务会被拒绝，因为那样聊天又会卡住。

### 子代理带着信息出发

子代理看不到聊天，也没有记忆，所以交接单由主代理来写。交接单的 `context` 不足 120 个字符，或者提到了另一个正在运行的子代理在改的文件，每轮都会被退回一次，并附上缺了什么的说明。第二次提交一定放行。

放行的交接单会在主代理自己写的内容后面附上一份简报：

- 你最近 6 条消息的原文，这样"别动 X"这类话能原样传到子代理手里；
- `USER.md` 和 `MEMORY.md`，因为 Hermes 不给子代理加载记忆；
- 正在运行的其他子代理，以及它们在改的文件；
- 这个对话里最近没做完的任务：查到了什么、卡在哪里、收到过哪些修正、改过哪些文件、日志在哪；
- 几条工作规则：只做份内的事；同一个错误出现两次就停；不要瞎猜，也不要绕过拦截；危险改动要能回退；汇报成功之前先核实真实结果；
- 默认的结果格式（`output_schema`）：`status`（`completed`、`partial`、`blocked`、`needs_decision` 或 `failed`）、`summary`、`evidence`、`blockers`，需要拍板时再加 `question` 和 `options`。任务自带格式的，不会被覆盖。

### 子代理不空转、不瞎猜

Hermes 能发现子代理在重复，但对子代理只提醒、不叫停。插件会计数：子代理用同一个工具、同样的参数，得到同样的结果，第 3 次提醒它，第 5 次叫停。次数按整个运行累计，不要求连续；比较的是结果的第一行，长数字和哈希会被遮掉，所以只是请求 ID 变了，仍然算同一个结果。叫停之后，它的工具调用一律被拒绝，只能以 `blocked` 汇报。总步数上限仍然由 Hermes 管（`delegation.max_iterations`）。

子代理也没法问你任何事：Hermes 不给它 `clarify` 工具，结束之后也不能再唤起。所以当它碰到不该由它来做的选择（两种都行的方案、改生产环境、花钱、删数据），而交接单里又没有答案时，它会停下来，以 `needs_decision` 汇报，附上一个问题和几个选项。Hermes 会按结果格式检查这份汇报，缺了问题就让它再补一次。

某一步被 Hermes 的审批或你配的 hook 拦下，或者在等一个这次运行里没人能给的审批，也是同样处理。子代理会当场收到提示（hook 拦下时，提示出现在它的下一个工具结果上）：不要重试，也不要换个办法达到同样的效果，把问题带回来。写着"用户拒绝"的拦截也可能是自动的：在某些配置下，Hermes 会不问任何人就拒绝子代理的危险命令。

主代理看得到整段对话，往往早就知道答案，直接接着做就行。真正该由你决定的事（偏好、风险、花钱、被拦下的命令），它会用一条简短的消息问你，并列出选项。不管哪种，它都会带着答案和上一个任务的记录，派一个新的子代理接着做。如果你已经听到了问题，而它到你下一条消息时还没跟进，这条消息会提醒它一次。

### 结果有核实、能送到、有记录

子代理做完之后，主代理下一轮开头会收到一条关于它的提示：子代理给出的证据；或者提醒它声称成功却没有证据、没做完、因为原地打转被叫停。最后这种情况，主代理会用一句话告诉你哪里卡住了、接下来怎么办。主代理自己主动停掉的任务，会注明是主动停的，不算失败。

后台子代理的结果会单开一轮交给主代理。如果这一轮还在跑的时候你发了消息，Hermes 会把你的消息并进这一轮，主代理往往只回你这句。所以插件会一直把这个结果标记为"还没汇报"，直到展示过它的那一轮以一次没被你打断的回复结束：

- 你发消息之后，主代理如果还调用了工具，那个工具的结果里会提醒它：回你的话，同时在同一条回复里汇报结果；
- 如果它没调工具就直接回了你，插件会再唤醒它一次，让它补一条简短的汇报；已经说过的话就保持沉默。唤醒走的是 Hermes 的消息注入，会排在正在运行的那一轮后面，绝不会插进去；
- 如果 Hermes 直接中断了那一轮，或者不允许唤醒，就由你的下一条消息带上这条提醒。

中间你每插进一条消息，都会重新提醒一次，所以连着发好几条也不会把结果弄丢。每个结果最多唤醒 2 次，超过一天的结果不再追。

每个任务都记进一个 SQLite 账本：目标和状态、改过的文件、通过 `action='steer'` 发过的修正、实时日志路径、结束时的情况（摘要、证据、阻碍和它提出的问题），以及你是否已经听到结果。Hermes 没法把结束的子代理再唤起来，所以同一个对话里的下一个子代理会拿到没做完的那个任务的记录。Hermes 如果在任务中途重启，主代理会被告知哪些任务丢了、副作用不确定、动过哪些文件。待送的提示和还没汇报的结果也都存在账本里，重启不会丢。

账本在 `<hermes home>/plugin-data/proactive-agent/ledger.db`，每个对话保留最近 20 个已结束的任务，最长 7 天。

## 原则

- **只跟主代理说话。** 插件只往主代理的上下文里加提示；为了补上被你打断的汇报，会用一条内部消息唤醒主代理。它从不往你的聊天里发消息，你听到什么由主代理决定。
- **不靠定时器。** 没有进度播报。每条提示都由发生的事情触发（一轮开始、工具返回、子代理结束、一轮结束时还有结果没汇报），而且只送一次。
- **不调用模型。** 所有检查都是普通代码。
- **Hermes 的事交给 Hermes。** 命令审批、过期文件保护、步数上限、循环提醒、把完成的结果交给主代理，都还是 Hermes 自己负责。插件只补它没管到的地方。
- **绝不碍事。** 插件自己出错时只记日志，工具调用照常进行。
- **只管你指定的地方。** 只有 `governed_platforms` 里的平台（默认 `["weixin"]`）和它们派出的子代理受管，其他平台和定时任务都不受影响。

## 配置

配置写在 `~/.hermes/config.yaml` 的 `plugins.entries.proactive-agent.settings` 下：

```yaml
plugins:
  entries:
    proactive-agent:
      allow_gateway_injection: true   # Hermes 的权限，见"安装"
      settings:
        governed_platforms: ["weixin"]
        turn_soft_seconds: 60
        turn_hard_seconds: 120
        terminal_auto_detach_seconds: 30
        handoff_min_context_chars: 120
        worker_repeat_limit: 5
```

| 配置项 | 默认值 | 作用 |
|---|---|---|
| `governed_platforms` | `["weixin"]` | 受管的平台，对主代理和它派出的子代理生效 |
| `turn_soft_seconds` | `60` | 到这个秒数时，提醒主代理回复或派活 |
| `turn_hard_seconds` | `120` | 到这个秒数后，这一轮暂停改文件和执行命令 |
| `terminal_auto_detach_seconds` | `30` | 终端命令跑超过这个秒数就转到后台（`0` 表示关闭） |
| `handoff_min_context_chars` | `120` | `context` 比这个短的交接单，每轮退回一次 |
| `worker_repeat_limit` | `5` | 同样的尝试做到第几次就叫停子代理（提前两次先提醒） |

每一项也可以用环境变量 `PROACTIVE_<配置名大写>` 来设，环境变量优先于配置文件。取值非法时只记日志，然后忽略。

## 常见问题

**会多烧 token 吗？**
插件自己不调用模型，只往主代理的上下文里加一些文字。为补上被打断的汇报而唤醒主代理，相当于主代理多跑一轮，开销和你多发一条消息差不多。

**会影响定时任务、命令行或其他平台吗？**
不会。只有 `governed_platforms` 里的平台受影响，定时任务和其他平台一切照旧。

**它会取代 Hermes 的审批或循环检测吗？**
不会，它们照常工作。Hermes 的审批拦下子代理的命令时，插件只是告诉子代理不要绕过，把问题带回来。

## 兼容性

- Hermes Agent 0.22 及以上（已在 v0.22.7 上测试）。
- Python 3.10 及以上，没有运行时依赖。
- CI 在 Python 3.10 到 3.14 上跑 `ruff check` 和 `pytest`。

## 开发

```bash
uv run --group dev pytest
uv run --group dev ruff check
```

能导入 `hermes_cli` 时，`tests/test_hermes_e2e.py` 会通过真实安装的 Hermes 运行插件，否则自动跳过。

```
proactive-agent/      插件本体，也是 Hermes 唯一安装的目录
├── secretary.py      单轮预算、长命令转后台、一轮中途你发来的消息
├── handoff.py        交接单检查、简报、工作规则和结果格式
├── workers.py        运行中的子代理、重复叫停、被拦下时的提示
├── reports.py        结果提示、待回答的问题、还没汇报的结果、重启恢复
├── ledger.py         任务账本
├── prompt.py         给主代理的系统提示词片段
├── context.py        判断调用方：主代理、子代理还是定时任务
├── config.py         配置
├── util.py           辅助函数
├── __init__.py       注册钩子
└── plugin.yaml       插件清单
tests/                每个模块一个测试文件，外加针对 Hermes 的端到端测试
```

## 给 AI 读的文档

[llms.txt](llms.txt) 是给 AI 代理和答案引擎看的简短索引，[llms-full.txt](llms-full.txt) 把整个项目写在了一个文件里。

## 许可证

[MIT](LICENSE)
