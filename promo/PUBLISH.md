# 发布文案

横版：`proactive-agent-promo.mp4`（1920×1080，2 分 12 秒，中文配音，中英双语字幕已压进画面），用于 B 站、YouTube、X
竖版：`proactive-agent-promo-vertical.mp4`（1080×1920，内容和配音相同，排版与字幕按竖屏重排），用于视频号、抖音、小红书
字幕文件：`subtitles.srt`（中英双语，可上传到支持外挂字幕的平台，也能让平台的搜索收录字幕文字）
封面：视频第一帧就是品牌卡（项目名和仓库地址），可直接当封面用

项目地址：https://github.com/JerryLiu369/hermes-proactive-agent

---

## 标题

中文（任选一个）：

1. 让 AI 助手像秘书一样做事：Hermes Proactive Agent
2. 交给 AI 的活，为什么总是石沉大海？｜Hermes Agent 开源插件
3. 聊天里的 AI 助手不再“已读不回”：Hermes Proactive Agent
4. 子代理不空转、不瞎猜、结果不漏报｜Hermes Agent 插件

English:

- Hermes Proactive Agent: make your chat assistant work like a reliable secretary
- Your AI assistant shouldn't go silent: an open-source plugin for Hermes Agent

---

## 简介（长版：B 站、YouTube 中文频道）

在聊天软件里给 AI 助手派一件慢活，它说“好的”，然后就再也没有声音了。

Hermes Proactive Agent 是 Hermes Agent 的开源插件。它让聊天里的助手像一位靠谱的秘书那样做事：耗时的慢活交给后台的子代理，助手自己随时在线；每件活做完，结果都会先核实、确实转达给你，并记入账本留档。

它解决六个常见问题：聊天冷场；子代理出发时两眼一抹黑；卡住的子代理原地打转或乱猜；“做完了”全凭自报；后台结果撞上你的新消息就漏报；网关一重启前因后果全丢。

插件做的事：
- 聊天始终保持响应：每一轮满 60 秒提醒助手先回复你或派活，满 120 秒暂停改文件和跑命令；终端命令跑满 30 秒自动转入后台。
- 子代理出发前心里有数：交代不足 120 个字符会退回一次；放行的任务自动附上你最近 6 条消息的原话、USER.md 和 MEMORY.md、正在运行的其他子代理、未完成任务的记录、做事规矩和回报格式。
- 不空转、不瞎猜：同一步骤拿到同样结果，第 3 次警告，第 5 次叫停；遇到不该它拍板的事，以 needs_decision 带着问题和选项回来。
- 结果有核实、有交代、有记录：没证据的“完成”会被标出；没转达给你的结果一直标着“尚未告知”；每个任务都记进 SQLite 账本，网关重启后也知道哪些任务中断了、动过哪些文件。

全部由普通 Python 代码完成：零模型调用，只走 Hermes 的公开接口，只跟助手说话，从不直接往你的聊天里发消息。默认只管理微信（weixin）平台，可在 governed_platforms 里改。

安装：
hermes plugins install JerryLiu369/hermes-proactive-agent/proactive-agent
hermes plugins enable proactive-agent

GitHub：https://github.com/JerryLiu369/hermes-proactive-agent（MIT 开源，欢迎 Star）

00:00 开场：派出去的活，没了回音
00:10 六件糟心事
00:23 Hermes Proactive Agent 是什么
00:34 聊天始终保持响应
00:51 子代理出发前心里有数
01:10 不空转、不瞎猜
01:26 有核实、有交代、有记录
01:44 还是那件慢活，这一次
01:53 设计原则与安装

## 简介（短版：视频号、抖音、小红书）

给 AI 助手派活，它说“好的”就再没回音？Hermes Proactive Agent 让助手像秘书一样做事：慢活交给后台子代理，助手随时在线；子代理出发有交代、卡住会叫停、该你定的会来问你；结果先核实再转达，全部记进账本。Hermes Agent 开源插件，两行命令安装。GitHub 搜索 Hermes Proactive Agent。

## Description (English: YouTube, X, Reddit)

You hand your chat assistant a slow job. It says "Sure"… and goes silent.

Hermes Proactive Agent is an open-source plugin for Hermes Agent that makes the assistant in your chat app work like a reliable secretary: slow work goes to background subagents, the assistant stays reachable, and every result is checked, delivered to you, and recorded.

- Responsive chat: a reminder at 60 s per turn, editing and commands paused at 120 s, terminal commands moved to the background after 30 s.
- Informed subagents: thin handoffs (under 120 characters) are sent back once; every handoff gets your last 6 messages, USER.md and MEMORY.md, running subagents, unfinished-task records, working rules and a report format.
- No spinning, no guessing: same step and same result, a warning on the 3rd repeat and a stop on the 5th; choices that aren't the subagent's to make come back as needs_decision with a question and options.
- Checked, reported, remembered: unproven "done" is flagged, results stay "unreported" until you've actually heard them, and every task is kept in a SQLite ledger that survives a gateway restart.

Plain Python, no model calls, public Hermes interfaces only. MIT licensed.

Install:
hermes plugins install JerryLiu369/hermes-proactive-agent/proactive-agent
hermes plugins enable proactive-agent

https://github.com/JerryLiu369/hermes-proactive-agent

## 标签 / Tags

Hermes Agent, Hermes Proactive Agent, AI Agent, AI 助手, 子代理, subagent, 多智能体, multi-agent, Agent 插件, 开源, open source, 微信 AI 助手, Nous Research, LLM, 自动化, Python

话题：#AIAgent #HermesAgent #开源项目 #AI助手 #智能体

## 置顶评论

项目地址：https://github.com/JerryLiu369/hermes-proactive-agent
两行命令安装，需要 Hermes Agent 0.22 及以上。觉得有用欢迎 Star，有问题欢迎提 Issue。

---

## 旁白全文（中英对照）

| 中文 | English |
|---|---|
| 你在聊天里，交给助手一件慢活。 | You hand your assistant a slow job in chat. |
| 它说“好的”，然后——就再也没有声音了。 | It says “Sure”… and then goes silent. |
| 派出去的子代理，两眼一抹黑； | Subagents set off knowing nothing; |
| 卡住了，就原地打转； | when they get stuck, they go in circles; |
| 说“做完了”，全凭自报； | “Done” is only their word for it; |
| 结果撞上你的新消息，就漏报了； | a result that lands with your new message never reaches you; |
| 网关一重启，前因后果，全都丢了。 | and a gateway restart wipes out the whole story. |
| Hermes Proactive Agent，让聊天里的助手，像一位靠谱的秘书那样做事： | Hermes Proactive Agent makes your chat assistant work like a reliable secretary: |
| 慢活交给后台的子代理，它自己，随时在线。 | slow work goes to background subagents, and the assistant stays reachable. |
| 每一轮对话，都有时间预算。 | Every turn has a time budget. |
| 满六十秒，提醒助手先回复你，或者把剩下的活派出去； | At 60 seconds, the assistant is reminded to answer you or hand off the rest; |
| 满一百二十秒，这一轮就暂停改文件、跑命令。 | at 120 seconds, editing files and running commands pause for that turn. |
| 终端命令跑满三十秒，自动转入后台：仍在运行，并非失败。 | A terminal command still running after 30 seconds moves to the background: still running, not failed. |
| 派活之前，插件先把关：交代太单薄，退回一次。 | Before a handoff, the plugin checks it: a thin brief is sent back once. |
| 放行的任务，自动附上一份背景材料： | Every handoff that goes through gets a briefing attached: |
| 你最近六条消息的原话、你的记忆文件、正在运行的其他子代理、上一个没做完的任务记录， | your last six messages word for word, your memory files, other running subagents, the record of unfinished work, |
| 还有做事规矩，和统一的回报格式。 | plus working rules and one report format. |
| 同一个步骤，拿到同样的结果：第三次警告，第五次叫停。 | Same step, same result: a warning the 3rd time, a stop the 5th. |
| 遇到不该它拍板的选择，它不瞎猜，带着问题和选项回来。 | Facing a choice that isn’t its to make, it doesn’t guess: it comes back with a question and options. |
| 能定的，助手自己定；该你拿主意的，助手来问你。 | The assistant settles what it can, and asks you what only you can decide. |
| 子代理说完成了？助手先看证据，再向你报喜。 | A subagent says it’s done? The assistant checks the evidence before telling you. |
| 后台结果撞上你的新消息，会一直标着“尚未告知”，直到真正转达给你。 | A result that collides with your new message stays marked “unreported” until it really reaches you. |
| 每个任务都记进 SQLite 账本；网关重启了，也知道哪些任务中断、动过哪些文件。 | Every task goes into a SQLite ledger; after a gateway restart, it knows what was cut off and which files it touched. |
| 还是开头那件慢活。这一次，活交给后台的子代理，助手一直在线； | The same slow job as before. This time the work goes to background subagents and the assistant stays reachable; |
| 结果核实过，才交到你手上。 | the result reaches you only after it has been checked. |
| 这一切，都由普通代码完成：零模型调用，只走公开接口，只跟助手说话。 | All of it is plain code: no model calls, public interfaces only, and it talks only to the assistant. |
| 两行命令装上它。在 GitHub 搜索 Hermes Proactive Agent，让你的助手，随时在线。 | Two commands to install. Search “Hermes Proactive Agent” on GitHub, and keep your assistant reachable. |
