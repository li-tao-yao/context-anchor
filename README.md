# Context Anchor

**适用于 Google Antigravity 桌面端（及 CLI / IDE）的轻量级单会话工作记忆锚定插件。**

> A lightweight, zero-dependency working memory ledger plugin for Google Antigravity that prevents context rot, compaction amnesia, and `/btw` branch memory loss in long multi-turn conversations.

---

## 为什么需要 Context Anchor？

跨会话记忆库（如 `Mem0`、`Graphiti`、`OpenViking`、`engramory`）解决的是“下次开新会话时去检索历史”；而在 **单个超长对话**（比如持续 40～100 轮的设计方案打磨、复杂调试、长文架构讨论）中，模型真正的失忆原因有三个：

1. **自动压缩丢失硬参数**：长对话触发上下文压缩后，早期敲定的具体尺寸、公差、边界条件、否决理由很容易被过度概括甚至丢掉。
2. **上下文腐化（Lost in the Middle）**：几十轮来回修改后，v1～v8 的作废草稿全堆在历史里，模型容易串版本或重新提出早就否决过的方案。
3. **重型 MCP 工具容易“忘了查”**：依赖模型主动调工具查库时，高频迭代经常不触发查询；向量检索还可能把旧草稿一起召回。

**Context Anchor 的解法：**
- **唯一的“原地覆写”工作账本（`memory_ledger.md`）**：只保留最新生效结论与不可违背的红线约束，旧值直接覆盖出正文，体积硬控制在 `<= 120 行 / 6 KB`。
- **智能增量 + 压缩感知 + 周期重锚定**：借助 Antigravity 原生 `PreInvocation` Hook，在必要时刻把账本推到上下文最末端（注意力最强处），其余轮次零重复注入。
- **不污染工作区 & 支持 `/btw` 续接继承**：默认按对话 ID 存放在 Antigravity 的会话 Artifact 目录中，纯文本对话也能用；使用 `/btw` 升级成独立续接对话时自动继承父对话账本并继续独立演化。

---

## 核心行为与触发规则

| 场景 / 阶段 | 行为 | 说明 |
| :--- | :--- | :--- |
| **第 1 ～ 6 轮** | **完全静默** | 不弹窗、不建文件、不注入多余提示，短平快聊天零打扰。 |
| **第 7 轮起（未建账本）** | **静默自检（每 6 轮复检一次）** | Hook 提醒模型评估是否有重要约束/共识/多步任务需要记录；若是闲聊则不建账本、也不向你提及。 |
| **手动随时开启** | **立刻创建并开始跟踪** | 发送 `/context-anchor start` 或直接说“开启记忆账本” / “开启 context-anchor”。 |
| **账本内容变化时** | **立即注入** | 检测到 `memory_ledger.md` 的 SHA-256 哈希变化（含你自己在侧栏手改）后立刻重注入。 |
| **发生上下文压缩时** | **立即注入** | 检测到 `transcript.jsonl` 新增 `CHECKPOINT`（自动压缩或 `/compact`）后立刻重新锚定。 |
| **未变化且未压缩** | **每 6 轮重锚定一次** | 间隔 `< 6` 轮时 Hook 返回 `{}`，绝不在对话历史里堆叠重复副本。 |
| **从 `/btw` 升级为新对话** | **自动继承父账本** | 检测到分叉会话后自动复制父对话的 `memory_ledger.md` 到新会话目录并立刻注入。 |

---

## 两种存储模式（自动兼容纯文本对话与工程项目）

1. **默认模式：会话级 Artifact（`session`）**
   - 路径：`<artifactDirectoryPath>/memory_ledger.md`
   - 适用：纯文本长对话、临时方案推演、新建项目不想往代码目录里塞多余文件。
   - 桌面端右侧 **Artifacts / Overview** 面板可直接点开查看和手改。
2. **可选模式：项目固定（`project`）**
   - 路径：`<workspace>/.agents/context-anchor/memory_ledger.md`
   - 适用：需要在**同一个项目目录的多个新会话**之间长期接力。
   - 使用 `/context-anchor pin`（或让模型执行 `pin`）一键将当前账本固定到项目，`/context-anchor unpin` 解除固定。

---

## 目录结构

```text
context-anchor/
├── plugin.json                      # Antigravity 插件清单
├── hooks.json                       # PreInvocation 生命周期钩子
├── config.default.json              # 默认阈值配置
├── assets/logo.svg                  # 插件图标
├── rules/AGENTS.md                  # 常驻工作记忆纪律
├── skills/context-anchor/SKILL.md   # /context-anchor 手动管理技能
├── scripts/
│   ├── common.py                    # 配置、transcript 解析、分叉检测、注册表
│   ├── pre_invocation.py            # PreInvocation 智能增量注入引擎（纯标准库）
│   ├── ledger_tool.py               # 账本初始化、体积校验、pin/unpin/pause/status 工具
│   ├── install.ps1                  # Windows 一键安装 / 更新 / 卸载脚本
│   └── install.sh                   # macOS / Linux 安装脚本
└── tests/
    └── test_context_anchor.py       # 自动化单元测试套件
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

> 新建任意本地项目目录时，只要该插件在全局目录处于启用状态，即**零配置自动生效**。

### 2. macOS / Linux 安装

```bash
chmod +x ./scripts/install.sh
./scripts/install.sh
```

### 3. 更新与维护

```powershell
git pull
python -m unittest discover -s tests -v
.\scripts\install.ps1
```

如需自定义阈值（如调整静默轮数或锚定间隔），不要改仓库文件，直接新建 `C:\Users\<用户名>\.gemini\antigravity\context-anchor-state\config.json` 覆盖对应字段即可，插件升级时不会丢失个人配置：

```json
{
  "auto_detect_after_turns": 6,
  "auto_detect_recheck_turns": 6,
  "reanchor_every_turns": 6,
  "max_lines": 120,
  "max_bytes": 6144
}
```

### 4. 卸载

```powershell
.\scripts\install.ps1 -Uninstall
```

---

## `/context-anchor` 常用指令

在 Antigravity 桌面端输入框直接输入或对模型说出以下需求即可：
- `/context-anchor start`（或“开启记忆账本”）：立刻建立并写入当前对话账本
- `/context-anchor compact`（或“整理记忆账本”）：合并精简账本，清理已过时条目
- `/context-anchor pin`：将当前账本固定到工作区 `.agents/context-anchor/memory_ledger.md`
- `/context-anchor unpin`：取消工作区固定，还原为仅会话内生效
- `/context-anchor pause` / `resume`：临时暂停或恢复当前账本注入
- `/context-anchor status`：查看当前模式、行数/字节数及继承来源

---

## 隐私与安全

- 所有状态与账本文件**100% 存储在本地**，不调用任何外部模型或网络接口，不上传任何数据。
- 内置纪律明确禁止在 `memory_ledger.md` 中写入密码、API Key、Token、Cookie 或私钥明文。
- Hook 脚本采用 **Fail-Open** 设计：任何异常均静默输出 `{}` 并以退出码 `0` 结束，绝不阻塞主对话流程。

## License

MIT
