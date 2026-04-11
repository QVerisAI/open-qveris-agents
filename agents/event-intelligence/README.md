# Event Intelligence Agent

事件情报官 — 金融 / 产业事件的语义检索、定时推送与深度分析。

## 功能

| 能力 | 说明 | 触发方式 |
|------|------|---------|
| 定时推送 | 按关键词 + 时间间隔周期性推送最新事件摘要；无新事件时静默跳过 | "开始推送"、"启动事件推送" |
| 手动查询 | 搜索任意时间窗口内的事件列表 | "查最近 3 小时事件"、"搜半导体事件" |
| 事件详情 | 对推送列表里任意一条生成 8 板块投研报告（核心摘要 / 投资逻辑 / 推理链 / 产业链 / 传导路径 / 逻辑库 / 核心标的 / 关键风险） | "第 3 条详细看看"、"那个关于芯片的事件" |
| 每日统计 | 每 24 小时汇总 S / A 级事件数量 | 随推送一起自动启停 |
| 修改偏好 | 推送间隔、关键词动态调整 | "改为 15 分钟推送"、"关注新能源" |

## 数据源

事件数据通过 **QVeris 平台**调用 [deepseekdata](https://admin.deepseekdata.com) 的语义事件检索工具，**无需**独立的 deepseekdata API Key。Qveris 负责：

- 参数转发（keyword / 时间窗口 / pageNo / pageSize）
- 响应解包（含超大响应的 OSS 签名 URL 下载）
- 统一计费（每次 execute 10 credits；search 不计费）

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `ARK_API_KEY` | 是 | 火山方舟 Coding Plan API Key（主模型：Kimi K2.5） |
| `QVERIS_TOKEN` | 是 | QVeris 数据平台 API Token |
| `OPENCLAW_GATEWAY_PASSWORD` | 是 | OpenClaw Web UI 访问密码 |

## 部署

```bash
# 1. 配置环境变量
cp openclaw.env.example ~/.openclaw/openclaw.env
# 编辑填入实际 Key

# 2. 复制 workspace
cp -r workspace/* ~/.openclaw/workspace/

# 3. 复制配置
cp openclaw.json ~/.openclaw/openclaw.json

# 4. 启动
openclaw
```

本 skill 的 Python 脚本只依赖 stdlib（`json` / `urllib` / `os` / `dataclasses` / `typing`），**无需 pip install 任何额外包**。

## 测试

打开 Web UI 后，发送以下消息验证完整流程：

### 手动查询

```
查最近 24 小时的 AI 事件
```

预期：agent 调用 `event_query.search_events`，返回 top N 条事件摘要（按 S 级优先 + 时间倒序），格式如：

```
📡 事件推送 (HH:MM) —— 最近 1440 分钟共 X 条新事件

━━━━━━━━━━━━━━━━━━━━━━━━
[1] <事件标题>
    ⏰ 2026-04-11 10:30:00 ｜ 信号等级: S级
    📌 一句话总结: ...
    📝 事件摘要: ...
```

### 事件详情

看到推送列表后，发送：

```
第 1 条详细看看
```

预期：agent 调用 `event_query.get_event_detail`，输出 8 个板块的完整投研报告（核心摘要 / 投资逻辑与多空博弈 / 推理验证链路 / 产业链全景 / 传导路径 / 逻辑库匹配 / 核心标的表 / 关键风险）+ 附录（历史案例）。

### 定时推送

```
开始推送 AI 事件，5 分钟一次
```

预期：agent 创建 cron 定时任务，立即执行一次推送，同时启动每日统计 cron。

### 修改偏好

```
关注半导体
```

预期：agent 把 `state/push_config.json` 里的 `keyword` 改成 "半导体"，立即用新关键词跑一次推送。

### 停止推送

```
停止推送
```

预期：移除事件推送和每日统计的 cron，`state/push_config.json` 里 `active=false`。

## 目录结构

```
workspace/
├── SOUL.md          # 助手人格（事件情报官）
├── IDENTITY.md      # 身份信息
├── AGENTS.md        # 行为规则
├── TOOLS.md         # 环境配置（路径 + Qveris 说明）
├── USER.md          # 用户信息
├── HEARTBEAT.md     # 定时任务
└── skills/
    └── event-intelligence/
        ├── SKILL.md          # 技能总控（触发逻辑、推送模板、详情 8 板块结构）
        ├── event_query.py    # 业务函数 search_events / get_event_detail / daily_event_summary
        ├── qveris_client.py  # Qveris REST 客户端（stdlib-only）
        └── state/
            ├── push_config.json   # 推送运行时配置（间隔 / 关键词 / 上次时间 / 统计开关）
            └── push_history.json  # 最近几批推送的事件缓存（用于"第 X 条详细看看"定位）
```

## 模型配置

默认使用火山方舟 Coding Plan：
- **主模型：** Kimi K2.5（`ark/kimi-k2.5`）
- **备选：** Ark Code Latest（`ark/ark-code-latest`）

如需切换模型，修改 `openclaw.json` 中的 `agents.defaults.model`。
