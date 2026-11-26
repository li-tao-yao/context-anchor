# Context Anchor

**适用于 Google Antigravity 桌面端（及 CLI / IDE）的轻量级 L1 + L2 分层工作记忆插件。**

> A lightweight, zero-dependency two-tier (L1 working ledger + L2 topic memories) memory plugin for Google Antigravity that prevents context rot, compaction amnesia, lazy recall, and `/btw` branch memory loss in long multi-turn and multi-version workflows.

---

## 为什么采用 L1 + L2 分层架构？（v0.3.0）

在超长对话和极多版本迭代任务（如专利撰写、教材/论文多轮修订、复杂系统调试）中，纯单文件记忆容易写满，而纯外部记忆库（仅靠按需读文件或向量检索）容易出现**模型偷懒不查（Lazy Recall）**或**误召回已作废旧版本**的问题。

**Context Anchor v0.3.0 的解法：**
- **L1 常驻工作账本（`memory_ledger.md`）**：原地覆写当前目标、**不可妥协的高危硬约束（绝不下沉）**、当前版本基线、否决事项与下一步，并在底部维护一张自动生成的 **L2 专题索引表**。默认容量按中英文公平计费（`<= 120 行 / 4000 字符 / 12 KB`），解决 UTF-8 中文 3 字节惩罚问题。
- **L2 专题记忆卡（`memories/*.md`）**：按主题存放大块领域明细（如附图标记表、术语符号表、分章进度、排除清单、审查意见避坑点），带极简 YAML Frontmatter（`title / trigger / keywords / updated / summary`）。平时 **0 token 占用**，每次改版原地覆写最新态，避免旧版本污染。
- **自动索引同步 + 轻量关键词触发提醒（`l2_hint`）**：
  - 通过 `ledger_tool.py sync-index` 自动刷新 L1 底部目录，消除手写索引漂移；
  - `PreInvocation` Hook 每轮自动用零依赖纯 Python 匹配当前输入与 L2 卡片的 `keywords`（原生支持中文子串与英文词边界），命中时直接注入精简提醒，提示模型先 `read_file` 再动手，无需重型数据库或外部中文分词库。
- **增量注入 + 压缩重锚 + `/btw` 隔离继承**：延续 Antigravity 原生 `PreInvocation` 增量机制，并在 `/btw` 分支或 `pin`/`unpin` 项目固定时自动同步隔离复制 `memory_ledger.md` 与全部 `memories/*.md`。

---

## 核心行为与触发规则

| 场景 / 阶段 | 行为 | 说明 |
| :--- | :--- | :--- |
| **普通对话（第 1 轮起）** | **默认自动建账本并注入** | `auto_start: true` 下首轮自动创建 `<artifactDirectoryPath>/memory_ledger.md` 并注入上下文；子代理会话自动跳过。 |
| **账本内容变化时** | **立即注入** | 检测到 `memory_ledger.md` 的 SHA-256 哈希变化（含侧栏手改或 `sync-index` 更新）后立刻重注入。 |
| **发生上下文压缩时** | **立即注入** | 检测到 `transcript.jsonl` 新增 `CHECKPOINT`（自动压缩或 `/compact`）后立刻重新锚定。 |
| **用户输入命中 L2 关键词** | **精准注入精简 `l2_hint`** | 即使 L1 未变化，只要本轮 `lastUserInput` 命中某张 `memories/*.md` 的 `keywords`，自动注入 2–4 行定向 `read_file` 提醒（同轮内多次工具调用自动去重，不刷屏）。 |
| **未变化、未压缩、未命中 L2** | **每 6 轮重锚定一次** | 间隔 `< 6` 轮时 Hook 返回 `{}`，绝不在对话历史里堆叠重复副本。 |
| **`/btw` 分支隔离与升级** | **隔离复制 L1 + 全部 L2 卡片** | 开 `/btw` 时仅定位唯一真父对话并复制其非空白账本与 `memories/*.md`；分支内修改互不污染。 |
| **可选静默模式（`auto_start: false`）** | **前 6 轮静默，第 7 轮起自检** | 在用户配置覆盖 `"auto_start": false` 即可切换为按需触发。 |

---

## 两种存储模式与 L2 目录结构

1. **默认模式：会话级 Artifact（`session`）**
   - L1：`<artifactDirectoryPath>/memory_ledger.md`
   - L2：`<artifactDirectoryPath>/memories/*.md`
   - 适用：单线程长对话、临时方案推演、不希望往代码/项目目录写入辅助文件。
2. **可选模式：项目固定（`project`）**
   - L1：`<workspace>/.agents/context-anchor/memory_ledger.md`
   - L2：`<workspace>/.agents/context-anchor/memories/*.md`
   - 适用：专利撰写、教材修订、长期工程研发等需要**跨多个新会话接力**的超长项目，各项目天然隔离不串台。
   - 使用 `/context-anchor pin` 一键连同 `memories/` 固定到工作区，`/context-anchor unpin` 解除固定并可回拷到会话目录。

### L2 专题记忆卡格式示例（`memories/figure_labels.md`）

```markdown
---
title: 附图标记与部件名称表
trigger: 修改附图、零件标号或实施例部件名称前必读
keywords: [附图, 标号, 部件, 实施例]
updated: 2026-11-26
summary: 统一管理全部附图标记与最新命名，防止跨章节编号冲突
---

# 附图标记与部件名称表

- 100：承载基座
- 101：升降导向机构
```

---

## 目录结构

```text
context-anchor/
├── plugin.json                      # Antigravity 插件清单（v0.3.0）
├── hooks.json                       # PreInvocation 生命周期钩子
├── config.default.json              # 默认阈值与 L2 配置
├── assets/logo.svg                  # 插件图标
├── rules/AGENTS.md                  # 常驻 L1 + L2 工作记忆纪律
├── skills/context-anchor/SKILL.md   # /context-anchor 管理技能
├── scripts/
│   ├── common.py                    # 配置、Frontmatter 解析、L2 索引与匹配、分叉检测
│   ├── pre_invocation.py            # PreInvocation 智能增量与 L2 关键词提醒引擎（纯标准库）
│   ├── ledger_tool.py               # init/check/sync-index/memory-save/list/delete/pin/unpin/status
│   ├── install.ps1                  # Windows 一键安装 / 更新 / 卸载脚本
│   └── install.sh                   # macOS / Linux 安装脚本
└── tests/
    └── test_context_anchor.py       # 自动化单元测试套件（29 项用例）
```

---

## 安装与日常维护（Antigravity 桌面端）

**环境要求**：仅需 Python 3.9+（纯标准库，**零第三方包、零数据库、零外部 API 费用**）。

### 1. 安装（Windows）

```powershell
git clone https://github.com/li-tao-yao/context-anchor.git
cd context-anchor
.\scripts\install.ps1
```

插件将被同步到 `C:\Users\<用户名>\.gemini\config\plugins\context-anchor\`。
打开 Antigravity 桌面端左侧 **Customizations → Installed**，即可看到 **Context Anchor** 卡片，支持随时一键启用/停用。

### 2. macOS / Linux 安装

```bash
chmod +x ./scripts/install.sh
./scripts/install.sh
```

### 3. 更新与测试

```powershell
git pull
python -m unittest discover -s tests -v
.\scripts\install.ps1
```

如需自定义配置，不要直接修改仓库文件，新建 `C:\Users\<用户名>\.gemini\antigravity\plugin_data\context-anchor\config.json` 覆盖字段即可，升级时自动保留：

```json
{
  "auto_start": true,
  "skip_subagents": true,
  "auto_detect_after_turns": 6,
  "auto_detect_recheck_turns": 6,
  "reanchor_every_turns": 6,
  "max_lines": 120,
  "max_chars": 4000,
  "max_bytes": 12288,
  "l2_index_max_entries": 40,
  "l2_keyword_hints": true,
  "l2_max_hint_matches": 3
}
```

### 4. 卸载

```powershell
.\scripts\install.ps1 -Uninstall
```

---

## `/context-anchor` 常用指令

- `/context-anchor start`（或“开启记忆账本”）：立刻建立并写入当前对话账本
- `/context-anchor compact`（或“整理记忆账本”）：精简 L1、将大块明细拆入 L2 专题卡并校验体积
- `ledger_tool.py memory-save / memory-list / memory-delete / sync-index`：管理 `memories/*.md` 并自动刷新 L1 索引表
- `/context-anchor pin` / `unpin`：连同 L2 卡片一起固定到工作区 `.agents/context-anchor/` 或解除固定
- `/context-anchor pause` / `resume`：临时暂停或恢复当前账本注入
- `/context-anchor status`：查看当前模式、行数/字符数/字节数、L2 卡片数量与索引同步状态

---

## 隐私与安全

- 所有状态、L1 账本与 L2 专题卡片**100% 存储在本地**，不调用任何外部模型或网络接口。
- 内置纪律明确禁止在 `memory_ledger.md` 或 `memories/*.md` 中写入密码、API Key、Token、Cookie 或私钥明文。
- Hook 脚本采用 **Fail-Open** 设计：任何异常均静默输出 `{}` 并以退出码 `0` 结束，绝不阻塞主对话流程。

## License

MIT
