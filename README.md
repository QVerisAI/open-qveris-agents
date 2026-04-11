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

## 快速部署

### 前置条件

- [OpenClaw](https://github.com/openclaw/openclaw) 已安装（v2026.3.24+）
- Python 3.11+（带 pandas、numpy、requests、matplotlib）
- API Keys：见各 agent 目录的 `openclaw.env.example`

### 部署步骤

```bash
# 1. 克隆仓库
git clone https://github.com/QVerisAI/open-qveris-agents.git
cd open-qveris-agents

# 2. 选择要部署的 agent
cd agents/portfolio-health-check

# 3. 配置环境变量
cp openclaw.env.example ~/.openclaw/openclaw.env
# 编辑填入实际的 API Key

# 4. 复制 workspace 到 OpenClaw
cp -r workspace/* ~/.openclaw/workspace/

# 5. 复制配置（或合并到已有配置）
cp openclaw.json ~/.openclaw/openclaw.json

# 6. 安装 Python 依赖
pip install pandas numpy requests matplotlib

# 7. 启动 OpenClaw
openclaw
```

### Docker 部署

```bash
# 以 portfolio-health-check 为例
cd agents/portfolio-health-check

# 构建镜像
docker build -t qveris-agent .

# 配置环境变量
cp openclaw.env.example openclaw.env
# 编辑 openclaw.env 填入实际的 API Key

# 启动
docker compose up -d
```

> 各 agent 目录下包含 `Dockerfile` 和 `docker-compose.yml`，可直接使用或根据需要调整。

## 相关仓库

- [open-qveris-skills](https://github.com/QVerisAI/open-qveris-skills) — QVeris 技能代码（skill 级别）

## License

MIT
