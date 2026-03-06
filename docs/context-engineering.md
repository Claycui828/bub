# Context Engineering Architecture

> 本文档梳理 Bub 框架的上下文工程：system prompt 如何拼装、tool description 如何渐进披露、agent 之间如何传递上下文。

---

## 1. System Prompt 拼装流水线

### 1.1 总体架构

每次 LLM 调用时，`ModelRunner._chat()` 都会重新构建 system prompt。入口在 `src/bub/core/model_runner.py:236-247`：

```
_chat(prompt)
  → _render_system_prompt()     # 构建 system prompt
  → tape.run_tools_async(       # 发送给 LLM（Republic 负责拼接 tape history）
      prompt=prompt,            # 当前轮的 user message
      system_prompt=system_prompt,
      tools=tools,
    )
```

### 1.2 五段式 System Prompt

`_render_system_prompt()` (`model_runner.py:303-314`) 按序拼接 5 个 block，用 `\n\n` 连接：

```
┌─────────────────────────────────────────┐
│ 1. Base System Prompt                   │  ← BUB_SYSTEM_PROMPT 环境变量
├─────────────────────────────────────────┤
│ 2. Workspace AGENTS.md                  │  ← 工作区根目录的 AGENTS.md
├─────────────────────────────────────────┤
│ 3. Tool Prompt Block                    │  ← <tool_view> + <tool_details>
├─────────────────────────────────────────┤
│ 4. Skills Block                         │  ← <basic_skills> + <channel_skills>
├─────────────────────────────────────────┤
│ 5. Runtime Contract                     │  ← <runtime_contract> + <context_contract> + <response_instruct>
└─────────────────────────────────────────┘
```

| # | Block | 来源文件 | 动态性 | 估算 Token |
|---|-------|---------|--------|-----------|
| 1 | Base system prompt | `Settings.system_prompt` → env `BUB_SYSTEM_PROMPT` | 静态 (session 级) | 0~500+ |
| 2 | Workspace AGENTS.md | `src/bub/integrations/republic_client.py:39-48` | 静态 (每次从磁盘读) | 0~1000+ |
| 3 | Tool prompt block | `src/bub/tools/view.py:8-15` | **每步变化** (hint 展开) | 200~800+ |
| 4 | Skills block | `src/bub/skills/view.py:8-31` | **每步变化** (hint 展开) | 100~500+ |
| 5 | Runtime contract | `model_runner.py:362-384` | 静态 (硬编码) | ~150 |

### 1.3 各 Block 详解

#### Block 1: Base System Prompt

- 来源：`Settings.system_prompt`，可通过 `BUB_SYSTEM_PROMPT` 环境变量或 `bub.yaml` 配置
- 在 `AppRuntime.get_session()` 时传入 `ModelRunner`
- 可被 `handle_input(system_prompt=...)` 覆盖（用于 sub-agent 场景）

#### Block 2: Workspace AGENTS.md

- `read_workspace_agents_prompt(workspace)` 读取 `{workspace}/AGENTS.md`
- 每次调用 `_render_system_prompt()` 都从磁盘读取（允许运行时修改）
- 如文件不存在，返回空字符串，跳过该 block

#### Block 3: Tool Prompt Block

见 [Section 2: Progressive Tool View](#2-progressive-tool-view-渐进式工具披露)。

#### Block 4: Skills Block

- `render_compact_skills()` (`src/bub/skills/view.py:8-31`)
- Skills 从三个位置发现（优先级递减）：
  1. **Project**: `{workspace}/.agent/skills/`
  2. **Global**: `~/.agent/skills/`
  3. **Builtin**: 包内置 skills
- 每个 skill 是一个目录，包含 `SKILL.md`（YAML frontmatter + markdown body）
- 输出格式：
  ```xml
  <basic_skills>
  === [skill_name](/path/to/SKILL.md): One-line description ===
  (如果被 $hint 展开，此处插入 SKILL.md body)
  </basic_skills>
  <channel_skills>
  === [channel_name](/path/to/SKILL.md): Channel description ===
  </channel_skills>
  ```
- 展开机制与 tool 相同：模型输出 `$skill_name` 触发，下一轮 system prompt 包含完整 body

#### Block 5: Runtime Contract

`_runtime_contract()` (`model_runner.py:362-384`) 硬编码三段 XML：

- `<runtime_contract>`: 工具调用规则、comma 命令兼容、`$name` hint 语法
- `<context_contract>`: 上下文过长时使用 `tape.handoff` 的指引
- `<response_instruct>`: 要求模型通过 channel skill 发送响应

### 1.4 User Message 处理

用户输入的处理路径：

```
Channel.on_receive(text)
  → AppRuntime.handle_input(session_id, text)
    → SessionRuntime.handle_input(text)
      → tape.ensure_bootstrap_anchor()     # 首次运行时写入 session/start anchor
      → tape.fork_tape()                   # fork 一份 tape 隔离当前轮
        → AgentLoop.handle_input(text)
          → InputRouter.route_user(text)   # 判断是命令还是普通文本
            → 命令: 直接执行，返回结果
            → 文本: enter_model=True, model_prompt=text
          → ModelRunner.run(model_prompt)
            → _chat(prompt)                # prompt 即用户输入文本
```

**注入消息机制** (`model_runner.py:142-147`):

在多步循环中，如果有新用户消息（通过 `_message_queue` 注入），会被 prepend 到 prompt 前：

```
[user interjection]: new message 1
[user interjection]: new message 2

{original prompt}
```

### 1.5 完整 LLM API 消息结构

Republic 的 `tape.run_tools_async()` 最终构建的消息列表：

```
messages = [
  {"role": "system", "content": system_prompt},            # 五段式 system prompt
  {"role": "system", "content": handoff_context},          # (可选) 来自上一个 anchor 的 handoff state
  # --- 以下是 tape history（仅 last anchor 之后的条目）---
  {"role": "user",      "content": "previous user input"},
  {"role": "assistant", "content": "", "tool_calls": [...]},
  {"role": "tool",      "content": "tool result", "tool_call_id": "...", "name": "..."},
  {"role": "assistant", "content": "previous response"},
  # --- 当前轮 ---
  {"role": "user",      "content": prompt},                # 当前用户输入
]
```

消息转换逻辑在 `src/bub/tape/context.py:26-51` 的 `_select_messages()` 函数中。

---

## 2. Progressive Tool View 渐进式工具披露

### 2.1 设计目标

减少 system prompt 中的 token 消耗：大多数工具在大多数轮次中不被使用，因此只展示一行摘要；当模型需要某个工具的完整参数时，通过 `$hint` 语法触发展开。

### 2.2 核心组件

| 组件 | 文件 | 作用 |
|------|------|------|
| `ToolDescriptor` | `src/bub/tools/registry.py:40-48` | 存储工具元数据 (name, short_description, detail, tool, source) |
| `ProgressiveToolView` | `src/bub/tools/progressive.py:10-63` | 管理 compact/expanded 两阶段渲染 |
| `render_tool_prompt_block()` | `src/bub/tools/view.py:8-15` | 组合 compact + expanded 输出 |
| `HINT_RE` | `src/bub/core/model_runner.py:23` | 正则 `\$([A-Za-z0-9_.-]+)` 检测 hint |

### 2.3 两阶段渲染

**Phase 1: Compact Block** (始终包含)

`ProgressiveToolView.compact_block()` (`progressive.py:40-45`):

```xml
<tool_view>
  - bash: Run shell command
  - fs_read (command: fs.read): Read file content
  - fs_write (command: fs.write): Write file content
  - fs_edit (command: fs.edit): Edit file content
  - web_fetch (command: web.fetch): Fetch URL as markdown
  - web_search (command: web.search): Search the web
  - agent: Delegate a task to a sub-agent and return result
  - agent_status (command: agent.status): Check sub-agent status
  - tape_handoff (command: tape.handoff): Create anchor handoff
  - ...
</tool_view>
```

每行约 10-20 tokens，15 个工具大约 150-300 tokens。

**Phase 2: Expanded Block** (按需包含)

`ProgressiveToolView.expanded_block()` (`progressive.py:47-63`):

```xml
<tool_details>
  <tool name="bash">
    name: bash
    source: builtin
    description: Run shell command
    detail: Execute bash in workspace. Non-zero exit raises an error...
    schema: {"type": "function", "function": {"name": "bash", "description": "Run shell command", "parameters": {"type": "object", "properties": {"cmd": {"type": "string", "description": "Shell command"}, "cwd": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "Working directory"}, "timeout_seconds": {"type": "integer", "minimum": 1, "description": "Maximum seconds..."}}, "required": ["cmd"]}}}
  </tool>
</tool_details>
```

每个 expanded 工具约 100-350 tokens，取决于参数复杂度。

### 2.4 Hint 触发机制

1. 用户输入或模型输出中包含 `$bash`、`$fs.read` 等
2. `ModelRunner._activate_hints()` (`model_runner.py:326-335`) 用 `HINT_RE` 扫描文本
3. 对每个匹配，调用 `ProgressiveToolView.note_hint(hint)` → 加入 `self.expanded` 集合
4. 同时检查是否匹配 skill 名称，匹配则加入 `self._expanded_skills`
5. **下一次** `_render_system_prompt()` 时，expanded block 包含该工具的完整 schema

### 2.5 其他展开触发方式

- **命令执行**: 用户执行 `,bash ls` 等命令后，`InputRouter` 调用 `tool_view.note_selected(name)` (`src/bub/core/router.py`)
- **tool.describe 命令**: `,tool.describe name=bash` 也会触发展开
- **reset**: `ProgressiveToolView.reset()` 清空 `self.expanded`，在 `SessionRuntime.reset_context()` 时调用

### 2.6 Tool Name 规范化

内部名称使用 `.` 分隔 (如 `fs.read`)，LLM function calling 使用 `_` 替代 (如 `fs_read`)：

- `ToolRegistry.to_model_name(name)` (`registry.py:129-131`): `name.replace(".", "_")`
- Compact view 中显示: `fs_read (command: fs.read): Read file content`
- 模型调用时使用 `fs_read`，执行时映射回 `fs.read`

### 2.7 当前限制

1. **无 "始终展开" 机制**: 所有工具都从 compact 开始，包括 `bash`、`agent` 等核心工具。模型需要先输出 `$bash` 才能看到参数 schema，浪费一轮交互。
2. **Schema 在 system prompt 中**: Tool schema 以文本嵌入 system prompt，而非通过 LLM API 的 `tools` 参数传递。这是因为 progressive disclosure 需要自定义控制哪些工具展开。
3. **展开不可撤销**: 一旦展开，在当前 session 中保持展开（除非手动 `reset_context()`），可能导致 system prompt 膨胀。

---

## 3. Tape 与上下文窗口管理

### 3.1 Tape Entry 类型

Republic 的 `TapeEntry` 有以下 kind：

| kind | 用途 | LLM message 映射 |
|------|------|------------------|
| `message` | 用户/助手对话消息 | `{"role": "user/assistant", "content": "..."}` |
| `tool_call` | 模型发起的工具调用 | `{"role": "assistant", "tool_calls": [...]}` |
| `tool_result` | 工具执行结果 | `{"role": "tool", "content": "...", "tool_call_id": "...", "name": "..."}` |
| `anchor` | 阶段分隔标记 | 不直接映射；决定历史截断点 |
| `event` | 运行时事件（不进入 LLM） | 不进入 LLM messages |

转换逻辑在 `_select_messages()` (`src/bub/tape/context.py:26-51`)。

### 3.2 Anchor 截断机制

Republic 使用 `LAST_ANCHOR` 策略：只有最后一个 anchor 之后的 tape entries 会被传给 LLM。

```
[anchor: session/start]    ← 被截断
[message: user "hello"]    ← 被截断
[message: assistant "hi"]  ← 被截断
[anchor: handoff]          ← 截断点
[message: user "next task"] ← 传给 LLM
[tool_call: bash]           ← 传给 LLM
[tool_result: "output"]     ← 传给 LLM
```

### 3.3 Handoff State 传递

当存在 anchor 时，其 state 会被注入为额外的 system message：

1. `SessionRuntime.handle_input()` 读取 `tape.last_anchor_state()` (`service.py:74-83`)
2. 将 state 存入 `TapeContext.state[HANDOFF_STATE_KEY]`
3. `_select_messages()` 检查 `context.state["_handoff_state"]`
4. `_render_handoff_context()` (`context.py:54-70`) 渲染为：

```
[Handoff context from previous phase]
Summary: {summary}
Next steps: {next_steps}
State: {json_dump_of_extra_fields}
```

**当前 Handoff State Schema** (松散的 dict):

| 字段 | 用途 | 是否渲染 |
|------|------|---------|
| `summary` | 当前阶段总结 | 是 |
| `next_steps` | 下一步建议 | 是 |
| `owner` | 阶段所有者 | 被显式排除 |
| 其他 | 任意 key-value | 作为 JSON dump |

**`tape.handoff` 工具** (`builtin.py:421-431`):

```python
@register(name="tape.handoff", model=HandoffInput)
async def handoff(params: HandoffInput) -> str:
    # HandoffInput 只有 name, summary, next_steps 三个字段
    state = {}
    if params.summary: state["summary"] = params.summary
    if params.next_steps: state["next_steps"] = params.next_steps
    await tape.handoff(anchor_name, state=state or None)
```

### 3.4 Fork / Merge 机制

每次用户输入都会创建 tape fork，保证操作隔离：

```
SessionRuntime.handle_input(text)
  → tape.fork_tape()           # 创建 fork：复制当前 tape 文件
    → AgentLoop runs...        # 在 fork 上操作
  → (finally) store.merge()    # 将 fork 中新增的 entries 追加回主 tape
```

**fork** (`store.py:179-185`):
- 创建 `{source}__{uuid8}` 命名的新 tape
- `copy_to()`: 复制整个 tape 文件，记录 `fork_start_id`

**merge** (`store.py:193-201`):
- `copy_from()`: 只复制 `id >= fork_start_id` 的条目到目标 tape
- 删除 fork tape 文件
- 使用 per-target 锁防止并发冲突

---

## 4. Agent 委派上下文

### 4.1 委派流程

```
Parent Agent (session: "sess-123")
  → tool call: agent(prompt="do X", description="task X")
    → agent_delegate() (src/bub/tools/agent.py:93-191)
      → agent_id = "agent-1"
      → sub_session_id = "sess-123:sub:agent-1"
      → runtime.handle_input(sub_session_id, prompt)
        → AppRuntime.get_session(sub_session_id)
          → 创建全新 SessionRuntime:
            - 新的 TapeService（独立 tape 文件）
            - 新的 ToolRegistry（可配置 allowed_tools）
            - 新的 ModelRunner（可配置 model, system_prompt）
            - 新的 AgentLoop
        → session.handle_input(prompt)
          → fork_tape → run model loop → merge
      → result = loop_result.assistant_output (纯文本)
      → runtime.remove_session(sub_session_id)  # 释放内存，tape 文件保留
      → _format_result(record, tape_name=tape_name)
        → "agent_id: agent-1\ntape: tape_name\n{result text}"
```

### 4.2 Sub-Agent 继承与隔离

| 属性 | 继承自父 Agent？ | 可覆盖？ |
|------|----------------|---------|
| Model | 是（默认使用相同 model） | 是 (`params.model`) |
| System prompt | 是（默认使用相同 base prompt） | 是 (`params.system_prompt`) |
| Tools | 是（默认使用所有 builtin 工具） | 是 (`params.allowed_tools`) |
| Tape history | **否** — 全新 tape | — |
| Expanded tools | **否** — 从 compact 开始 | — |
| Expanded skills | **否** — 从 compact 开始 | — |
| Handoff state | **否** — 无继承 | — |
| Session context | **否** — 独立 session | — |

### 4.3 结果返回

`_format_result()` (`agent.py:286-297`) 返回纯文本：

```
agent_id: agent-1
tape: main:abc123ef
(agent result text or error)
```

**丢失的上下文**:
- Sub-agent 执行了哪些工具调用（名称、次数）
- 修改了哪些文件
- 执行了哪些 shell 命令
- 中间推理过程
- 遇到的错误和恢复策略

### 4.4 Background Agent

`run_in_background=True` 时：
- 立即返回 `agent_id`
- `asyncio.create_task()` 在后台执行
- 通过 `agent.status` 工具查询进度
- 完成后 tape 文件保留，可通过 `resume` 继续

### 4.5 Resume 机制

`resume=agent_id` 时：
- 复用原 `session_id`，Republic 会自动加载该 session 的 tape 历史
- 新 prompt 追加到已有 tape 上
- 保留了之前所有对话历史

---

## 5. 已知问题与改进路线图

### 5.1 Tool 描述不够灵活

**问题**: `bash`、`agent` 等核心工具缺少参数 schema 时，模型可能生成错误的 tool call，浪费一轮重试。

**改进**: 给 `ToolDescriptor` 添加 `always_expand: bool` 标记，标记的工具始终出现在 `<tool_details>` 中。

**涉及文件**:
- `src/bub/tools/registry.py:40-48` (ToolDescriptor)
- `src/bub/tools/progressive.py:47-63` (expanded_block)
- `src/bub/tools/builtin.py:175` (bash 注册)
- `src/bub/tools/agent.py:87-92` (agent 注册)

### 5.2 Handoff 信息丢失

**问题**: `HandoffInput` 只有 `summary` 和 `next_steps`，无法传递结构化上下文（修改了哪些文件、做了哪些决策）。

**改进**: 扩展 `HandoffInput` schema，增加 `files_modified`、`decisions` 等字段；增强 `_render_handoff_context()` 的渲染逻辑。

**涉及文件**:
- `src/bub/tools/builtin.py:73-77` (HandoffInput)
- `src/bub/tape/context.py:54-70` (_render_handoff_context)

### 5.3 Sub-Agent 结果缺少结构化信息

**问题**: `_format_result()` 返回纯文本，父 agent 无法了解子 agent 的具体操作。

**改进**: 在 `remove_session()` 前查询 sub-agent tape，提取 action summary（工具调用统计、修改文件列表）。

**涉及文件**:
- `src/bub/tools/agent.py:286-297` (_format_result)
- `src/bub/tools/agent.py:178-189` (foreground completion)

### 5.4 Prompt 拼装缺乏可观测性

**问题**: `_render_system_prompt()` 是简单的列表拼接，无法在 tracing 中看到各 block 的大小和内容。

**改进**: 引入 `PromptBuilder` 类，提供 `debug_info()` 输出各 block 元数据到 tracing span。

**涉及文件**:
- 新建 `src/bub/core/prompt_builder.py`
- `src/bub/core/model_runner.py:303-314` (_render_system_prompt)
- `src/bub/core/model_runner.py:236-247` (_chat, tracing metadata)


### 5.5 Tools展开时导致的kv cache失效。

**问题**: 当使用 $hint 工具时会在system prompt中塞入该工具的完整schema，导致kv cache失效。需要考虑额外的处理方法来处理展开后的tool，比如将展开后的tool response放入tool response中


### 5.6 Tools expand中信息不足

**问题**： 当前Tools expand展开的详细解释较为简单，不含when to use，when not to use的更详细的结构体，不方便未来对于渐进式披露内容的扩充。


### 5.7 对于部分工具的描述不周，导致anchor插入时机，上文做的事和下文做的事描述不清，并且在插入点后的上下文组织需要更加合理。确保agent被委派后能够更明确接下来的任务。


### 5.8 整体prompt优化

**问题**： 目前的prompt有待提升，参考你对与自己相同工具的system prompt与设计（如Read 对应fs.read）,优化当前prompt，确保key points和when to use合理。结合bub自己的设计来优化独有工具


### 5.9 工具扩充

**问题**： 基于你自己的设计扩充必要的工具集，如fs.grep, fs.glob等

