# Open QVeris Agents

可部署的 AI Agent 方案集合，基于 [OpenClaw](https://github.com/openclaw/openclaw) 运行时 + [QVeris](https://qveris.ai) 数据平台。

每个 agent 是一个完整的、拿来就能跑的垂直领域解决方案，包含：
- **workspace/** — OpenClaw workspace 文件（人格、行为规则、技能、脚本）
- **openclaw.json** — OpenClaw 配置（模型、网关、工具）
- **openclaw.env.example** — 环境变量模板

## Agents

| Agent | 描述 | 状态 |
|-------|------|------|
| [portfolio-health-check](agents/portfolio-health-check/) | 投资组合健康检查助手：快速诊断 → 深度诊断 → 优化处方 | ✅ 可用 |
| [event-intelligence](agents/event-intelligence/) | 事件情报官：金融/产业事件语义检索、定时推送、深度投研报告（8 板块） | ✅ 可用 |

## 部署

每个 agent 是 OpenClaw 多 agent 架构中的一个**独立 agent 实例**，拥有自己的 workspace、模型配置和渠道路由。不要直接覆盖默认 workspace。

### 前置条件

- [OpenClaw](https://github.com/openclaw/openclaw) 已安装并初始化（v2026.3.24+）
- Python 3.11+
- API Keys：见各 agent 目录的 `openclaw.env.example`

### 部署步骤（以 event-intelligence 为例）

以下 5 步适用于所有 agent。将 `<AGENT>` 替换为具体 agent 目录名（如 `event-intelligence`、`portfolio-health-check`）。

```bash
# 0. 克隆仓库
git clone https://github.com/QVerisAI/open-qveris-agents.git
cd open-qveris-agents/agents/<AGENT>
```

#### 1. 新建独立 agent

```bash
openclaw agents add <AGENT>
```

这会在 `~/.openclaw/` 下创建该 agent 的目录结构。

#### 2. 复制 workspace

将仓库中的 workspace 内容复制到该 agent 的专属 workspace 目录：

```bash
mkdir -p ~/.openclaw/workspace-<AGENT>
cp -r workspace/* ~/.openclaw/workspace-<AGENT>/
```

#### 3. 更新主配置

确认 `~/.openclaw/openclaw.json` 的 `agents.list` 中已有该 agent 条目（`openclaw agents add` 通常会自动创建）。如需调整模型等参数，编辑对应条目：

```jsonc
{
  "agents": {
    "defaults": { /* 你已有的全局默认配置 */ },
    "list": [
      // ... 已有的 agent ...
      {
        "id": "<AGENT>",
        "name": "显示名称",
        "workspace": "/home/<user>/.openclaw/workspace-<AGENT>",
        "model": {
          "primary": "ark/kimi-k2.5",
          "fallbacks": ["ark/ark-code-latest"]
        }
      }
    ]
  }
}
```

如果你的 OpenClaw 还没配置过 Ark 模型 provider，还需要在 `models.providers` 中添加（参见各 agent 目录下的 `openclaw.json` 示例）。

#### 4. 补环境变量

将 agent 所需的环境变量追加到 `~/.openclaw/openclaw.env`：

```bash
cat openclaw.env.example >> ~/.openclaw/openclaw.env
# 编辑填入实际的 API Key（去重已有的变量）
```

#### 5. 路由绑定

在 `~/.openclaw/openclaw.json` 的顶层 `bindings` 数组中，将该 agent 绑定到目标渠道：

```jsonc
{
  "bindings": [
    {
      "agentId": "<AGENT>",
      "match": {
        "channel": "feishu",
        "accountId": "<your-account>"
      }
    }
  ]
}
```

路由支持按渠道（channel）、账号（accountId）、群/会话（peer）等维度精确匹配。详见 [OpenClaw 多 agent 文档](https://github.com/openclaw/openclaw)。

#### 启动

```bash
openclaw
```

> 各 agent 的具体配置细节（模型、环境变量、Python 依赖等）见各自 README。

## 相关仓库

- [open-qveris-skills](https://github.com/QVerisAI/open-qveris-skills) — QVeris 技能代码（skill 级别）

## License

MIT
