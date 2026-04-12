# Portfolio Health Check Agent

投资组合健康检查助手 — 三阶段自动化诊断与优化。

## 功能

| 阶段 | 内容 | 数据来源 |
|------|------|----------|
| 快速诊断 | 标的识别、持仓确认表、集中度检查、总体评价 | QVeris identify |
| 深度诊断 | 波动率、相关性、回撤、因子暴露、风险旗标 | QVeris 行情 + 15 步量化计算 |
| 优化处方 | 基于约束的分层优化建议、压力测试、前后对比 | Phase 2 诊断结果 |

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `ARK_API_KEY` | 是 | 火山方舟 Coding Plan API Key（主模型：Kimi K2.5） |
| `QVERIS_TOKEN` | 是 | QVeris 数据平台 API Token |
| `OPENCLAW_GATEWAY_PASSWORD` | 是 | OpenClaw Web UI 访问密码 |

## 部署

> 通用流程见 [根目录 README](../../README.md#部署)，以下是本 agent 的具体配置。

#### 1. 新建 agent + 复制 workspace

```bash
openclaw agents add portfolio-health-check
cp -r workspace/ ~/.openclaw/workspace-portfolio-health-check/
```

#### 2. 主配置添加 agent

在 `~/.openclaw/openclaw.json` 的 `agents.list` 中加入：

```jsonc
{
  "id": "portfolio-health-check",
  "name": "投资组合健康检查助手",
  "workspace": "~/.openclaw/workspace-portfolio-health-check",
  "model": {
    "primary": "ark/kimi-k2.5",
    "fallbacks": ["ark/ark-code-latest"]
  }
}
```

#### 3. 环境变量

将 `openclaw.env.example` 中的变量追加到 `~/.openclaw/openclaw.env`（去重已有的）。

#### 4. 安装 Python 依赖

```bash
pip install pandas numpy requests matplotlib
```

#### 5. 路由绑定

在 `~/.openclaw/openclaw.json` 的 `bindings` 中绑定渠道：

```jsonc
{
  "agentId": "portfolio-health-check",
  "match": { "channel": "<your-channel>", "accountId": "<your-account>" }
}
```

#### 6. 启动

```bash
openclaw
```

## 测试

打开 Web UI 后，发送以下消息测试完整流程：

### Phase 1: 快速诊断

```
帮我看看这个组合：贵州茅台 30%，宁德时代 25%，招商银行 20%，中国平安 15%，现金 10%
```

预期：agent 调用 QVeris identify 获取标的信息，输出持仓确认表和快速诊断。

### Phase 2: 深度诊断

确认持仓后，发送：

```
继续深度诊断。换仓频率每月，仓位风格核心卫星，风险偏好稳健，投资期限3-5年
```

预期：agent 调用 `pipeline_main.py` CLI，输出波动率、相关性、回撤、因子暴露等量化指标。

### Phase 3: 优化处方

深度诊断完成后，发送：

```
继续优化。可投资市场A股，可使用工具股票和ETF，可追加资金10-30%，目标资产增值
```

预期：agent 调用 `prescription_main.py` CLI（含 `--internal`），输出分层优化建议和前后对比。

## 目录结构

```
workspace/
├── SOUL.md          # 助手人格（投资组合健康检查助手）
├── IDENTITY.md      # 身份信息
├── AGENTS.md        # 行为规则
├── TOOLS.md         # 环境配置（路径说明）
├── USER.md          # 用户信息
├── HEARTBEAT.md     # 定时任务
└── skills/
    └── portfolio-health-check/
        ├── SKILL.md                    # 总控 skill
        ├── portfolio-quick-diagnosis/  # Phase 1
        ├── portfolio-deep-diagnosis/   # Phase 2
        ├── portfolio-optimization/     # Phase 3
        └── scripts/
            └── portfolio-health-check/
                ├── pipeline_main.py        # Phase 2 CLI
                ├── prescription_main.py    # Phase 3 CLI
                ├── qveris_client.py        # QVeris 客户端
                ├── diagnosis.py            # 15 步诊断引擎
                └── ...
```

## 模型配置

默认使用火山方舟 Coding Plan：
- **主模型：** Kimi K2.5（`ark/kimi-k2.5`）
- **备选：** Ark Code Latest（`ark/ark-code-latest`）

如需切换模型，修改 `~/.openclaw/openclaw.json` 中该 agent 在 `agents.list` 里的 `model` 字段。
