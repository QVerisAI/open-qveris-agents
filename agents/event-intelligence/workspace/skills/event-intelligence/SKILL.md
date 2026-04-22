---
name: event-intelligence
description: 定时推送、手动获取事件资讯，并支持查询单条事件详情。
description_zh: 事件语义检索定时推送 + 单条详情查询
description_en: Scheduled event push notifications with detail drill-down
---

## Cron Session 约束

- 任何 cron / 定时事件如果需要绑定 session，必须显式写 `sessionTarget="isolated"`。
- 严禁使用 `sessionTarget="current"`。
- 严禁使用 `sessionTarget="session:xxx"`，以及任何 `session:*` 形式的已有 session 绑定。

# Event Intelligence — 事件情报技能

## 概述

本技能分为两部分：

1. **定时推送、手动获取**：按用户设定的时间间隔（默认 5 分钟），周期性调用事件语义检索 API，获取最新事件摘要并推送给用户。**无新事件时静默跳过，不发送消息。** 用户可以指定获取最近一段时间内的最新事件。
2. **事件详情查询**：当用户对某条推送的事件感兴趣时，通过 `eventId` 调用详情接口获取完整分析数据。
3. **每日事件统计**：每 24 小时自动统计 S 级、A 级事件数量，生成简报推送。随定时推送一起启停。

## 文件结构

```
event-intelligence/
├── SKILL.md              # 本文件，技能说明
├── event_query.py         # 业务函数（search_events / get_event_detail / daily_event_summary）
├── qveris_client.py       # Qveris REST 客户端（search + execute_tool）
└── state/
    ├── push_config.json   # 推送运行时配置（间隔、关键词、上次推送时间、每日统计开关等）
    └── push_history.json  # 最近几次推送的事件列表缓存（用于按序号/描述查详情）
```

## 前置条件

- Python 3.10+（仅依赖 stdlib，无需 pip 安装任何包）
- 网络可达 `https://qveris.ai`
- API Key 通过环境变量 `QVERIS_TOKEN` 读取，必须提前配置。事件数据通过 Qveris 平台调用 deepseekdata 语义事件检索工具，无需本地持有 deepseekdata 独立 API Key。

## 环境适配

### 路径约定

本技能中所有文件路径均 **相对于本 SKILL.md 所在目录**（即 `event-intelligence/`）。
执行命令前，agent 应先定位本技能目录：

```bash
SKILL_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]:-$0}")")" && pwd 2>/dev/null || pwd)"
```

如果 agent 是通过读取 SKILL.md 获得路径的，直接取其父目录即可。后续所有 `cd` 均使用该变量，不依赖任何硬编码绝对路径。

### Python 兼容

不同环境中 Python 命令可能是 `python3` 或 `python`，执行前先探测：

```bash
PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
if [ -z "$PY" ]; then echo "错误：未找到 python3 或 python，请先安装 Python 3.10+"; exit 1; fi
```

后续所有 Python 调用统一使用 `$PY` 代替 `python3` / `python`。

---

## 第一部分：定时推送

### 启动流程

用户说"开始推送"/"启动事件推送"/"开始定时推送"等触发本技能后：

1. **读取配置**：读取 `state/push_config.json`，获取当前推送参数。
2. **确认参数**：向用户确认以下参数（如果配置文件已有值，展示当前值并问是否需要修改）：
   - `interval_minutes`：推送间隔（默认 5 分钟）
   - `keyword`：语义检索关键词（默认 "AI"，用户可改为任意主题，比如：半导体、机器人、新能源）
   - `page_size`：每次推送返回的事件条数（默认 10）
3. **写入配置**：将确认后的参数写入 `state/push_config.json`。
4. **立即执行一次推送**：调用 `search_events()` 获取当前时间窗口内的事件，格式化后推送给用户（如果无事件则静默跳过）。
5. **设置定时任务**：利用 OpenClaw 的 cron 机制，按 `interval_minutes` 的间隔周期执行推送。
6. **同时启动每日统计**：创建一个每 24 小时执行一次的 cron 任务，用于统计 S/A 级事件数量（详见下方"每日事件统计"章节）。

### 推送执行逻辑

每次推送执行时：

1. 运行以下命令获取事件列表：
   ```bash
   cd "$SKILL_DIR"
   $PY -c "
   import json, sys
   sys.stdout.reconfigure(encoding='utf-8')
   from event_query import search_events
   result = search_events(keyword=\"\"\"<KEYWORD>\"\"\", minutes=<INTERVAL_MINUTES>, page_size=<PAGE_SIZE>)
   print(json.dumps(result, ensure_ascii=False, indent=2))
   "
   ```
2. 解析返回的 JSON，提取事件列表。
3. 如果 `total > 0`，按以下格式推送给用户：

   ```
   📡 事件推送 (HH:MM)  —— 最近 N 分钟共 X 条新事件（最多推送10条）

   ━━━━━━━━━━━━━━━━━━━━━━━━
   [1] 标题
       ⏰ 2026-04-09 10:30:00 ｜ 信号等级: S级
       📌 一句话总结: xxxxx（取自 original_summary 字段）
       📝 事件摘要: xxxxx（取自 summary 字段）

   ━━━━━━━━━━━━━━━━━━━━━━━━
   [2] ...

   💡 对某条感兴趣？直接说「第X条详细看看」或描述标题关键词即可查看完整分析。
   🔄 想换个主题？说「关注半导体」即可切换关键词。
   ⏱️ 想调整推送频率？说「改为15分钟推送一次」。
   ```

   **字段映射**：
   - 标题 ← `compliantTitle`
   - 时间 ← `eventPublishDate`
   - 信号等级 ← `signalLevel`
   - 一句话总结 ← `original_summary`（产业链维度的精炼总结）
   - 事件摘要 ← `summary`（事件核心要点概述）

   两个摘要都必须输出，不可省略。`original_summary` 偏产业链视角，`summary` 偏事件本身，互相补充。

   > **前缀清洗**：`event_query.py` 已在返回数据时自动去除 `original_summary` 和 `summary` 开头的"研报"/"研报指出"等冗余前缀及紧跟的标点，agent 直接使用返回值即可，无需额外处理。

4. 如果 `total == 0`，**静默跳过，不向用户发送任何消息**。仅更新 `state/push_config.json` 中的 `last_push_time`，不写入推送历史，不输出任何内容。
5. **缓存推送结果**（仅 `total > 0` 时执行）：将本次推送的事件列表写入 `state/push_history.json`，供后续详情查询使用（见下方"推送历史缓存"章节）。
6. 更新 `state/push_config.json` 中的 `last_push_time`。

### 推送历史缓存

每次推送成功后，必须将结果追加到 `state/push_history.json`。

**缓存规则：**

1. 每次推送结果作为一个 batch 存入 `batches` 数组，新 batch 插入数组头部（最新在前）。
2. `batches` 最多保留 `max_batches` 条（默认 3），超出时丢弃最旧的。
3. 每个 batch 中的事件保留推送时的序号（从 1 开始），与推送给用户时展示的 `[1] [2] [3]` 对应。

**batch 结构示例：**

```json
{
  "max_batches": 3,
  "batches": [
    {
      "push_time": "2026-04-09T10:30:00",
      "keyword": "AI",
      "events": [
        {
          "index": 1,
          "eventId": "76679",
          "compliantTitle": "某事件标题",
          "eventPublishDate": "2026-04-09 10:15:00",
          "signalLevel": "high",
          "original_summary": "产业链总结...",
          "summary": "摘要文本..."
        },
        {
          "index": 2,
          "eventId": "76680",
          "compliantTitle": "另一个事件",
          "eventPublishDate": "2026-04-09 10:20:00",
          "signalLevel": "medium",
          "original_summary": "...",
          "summary": "..."
        }
      ]
    }
  ]
}
```

**写入方式：**

每次推送执行完毕后，运行：

```bash
cd "$SKILL_DIR"
$PY -c "
import json
from datetime import datetime

history_path = 'state/push_history.json'
with open(history_path, 'r') as f:
    history = json.load(f)

new_batch = {
    'push_time': datetime.now().strftime('%Y-%m-%dT%H:%M:%S+08:00'),
    'keyword': '<KEYWORD>',
    'events': <EVENTS_LIST>
}

history['batches'].insert(0, new_batch)
max_b = history.get('max_batches', 3)
history['batches'] = history['batches'][:max_b]

with open(history_path, 'w') as f:
    json.dump(history, f, ensure_ascii=False, indent=2)
"
```

其中 `<EVENTS_LIST>` 是本次推送返回的事件列表（每个事件增加 `index` 字段，从 1 开始编号）。

### 修改推送间隔

当用户说"改为 15 分钟推送一次"/"推送间隔改成 30 分钟"等：

1. 解析用户指定的新间隔值。
2. 更新 `state/push_config.json` 中的 `interval_minutes`。
3. 重新设置 cron 定时任务为新间隔。
4. 同时在 `memory/YYYY-MM-DD.md` 中记录本次修改。
5. 回复用户确认："已将推送间隔调整为 XX 分钟。"

### 修改关键词

当用户说"关注半导体"/"推送关键词改为新能源"等：

1. 更新 `state/push_config.json` 中的 `keyword`。
2. 立即用新关键词执行一次推送。
3. 回复用户确认。

### 停止推送

当用户说"停止推送"/"关闭推送"等：

1. 移除事件推送的 cron 定时任务。
2. **同时移除每日统计的 cron 定时任务**（如果存在）。
3. 更新 `state/push_config.json`，将 `active` 设为 `false`。
4. 回复用户确认："已停止事件推送和每日统计。随时可以说'开始推送'重新启动。"

---

### 每日事件统计

#### 概述

与定时推送配套的 24 小时周期统计功能。启动推送时自动一并启动，停止推送时一并关闭。统计过去 24 小时内按信号等级（S 级、A 级）的事件数量，以简报形式推送给用户。

#### 启动方式

随定时推送一起自动启动，**不需要**用户单独触发。启动推送时：

1. 在设置事件推送 cron 的同时，额外创建一个 **每 24 小时执行一次** 的 cron 定时任务。
2. 该 cron 任务的执行内容是：调用 `daily_event_summary()` 并将结果格式化输出给用户。
3. 在 `state/push_config.json` 中记录 `daily_summary_active: true`。

#### 执行逻辑

每次执行时：

1. 运行以下命令获取统计数据：
   ```bash
   cd "$SKILL_DIR"
   $PY -c "
   import json, sys
   sys.stdout.reconfigure(encoding='utf-8')
   from event_query import daily_event_summary
   result = daily_event_summary(keyword=\"\"\"<KEYWORD>\"\"\", minutes=1440)
   print(json.dumps(result, ensure_ascii=False, indent=2))
   "
   ```
2. 解析返回的 JSON。
3. 如果 `total > 0`，按以下格式输出给用户：

   ```
   📊 每日事件统计 (HH:MM)
   ━━━━━━━━━━━━━━━━━━━━━━━━
   🔍 关键词: <KEYWORD>
   📅 统计周期: YYYY-MM-DD HH:MM ~ YYYY-MM-DD HH:MM（过去 24 小时）

   🔴 S 级事件: X 条
   🟠 A 级事件: Y 条
   📋 其他等级: Z 条
   ── 合计: N 条

   💡 想查看具体事件？说「查最近24小时的事件」。
   ```

4. 如果 `total == 0`，**静默跳过，不发送任何消息**（与事件推送保持一致）。

#### 停止方式

随定时推送一起关闭，无需单独操作。

---

### 手动查询事件

当用户说"查最近5小时的事件"/"最近有什么AI事件"/"搜一下半导体事件"等：

1. 解析用户指定的时间范围（如"5小时" → minutes=300），如未指定默认 60 分钟。
2. 使用当前配置的 keyword（或用户指定的关键词）调用 `search_events()`。
3. **按推送格式输出结果**（与定时推送的格式完全一致，参见"推送执行逻辑"第3步），但末尾的引导语替换为手动查询专用引导（见下方）。
4. **同样写入 `state/push_history.json`**，确保后续可以通过"第X条详细看看"查询详情。
5. 更新 `state/push_config.json` 的 `last_push_time`。
6. **后续引导**（追加在事件列表末尾）：
   - 有事件时：
     ```
     💡 对某条感兴趣？直接说「第X条详细看看」即可查看完整分析。
     🔍 想看其他时间段？说「查最近1小时」或「查最近24小时」。
     🔄 想换个主题？说「搜一下半导体事件」。
     ```
   - 无事件时：
     ```
     💡 最近 N 分钟/小时暂无新事件。想扩大范围？说「查最近24小时」。
     🔄 想换个主题？说「搜一下半导体事件」。
     ```

```bash
cd "$SKILL_DIR"
$PY -c "
import json, sys
sys.stdout.reconfigure(encoding='utf-8')
from event_query import search_events
result = search_events(keyword=\"\"\"<KEYWORD>\"\"\", minutes=<MINUTES>, page_size=<PAGE_SIZE>)
print(json.dumps(result, ensure_ascii=False, indent=2))
"
```

---

## 第二部分：事件详情查询

### 触发方式

用户可以通过以下任意方式触发详情查询：

| 用户说法示例                     | 匹配方式     |
|----------------------------------|-------------|
| "第3条详细看看"                  | 按序号匹配   |
| "那个关于芯片的事件看一下"         | 按关键词模糊匹配标题/摘要 |
| "长飞光纤那条"                   | 按标题中的实体名称匹配 |
| "上一轮推送的第1条"               | 指定历史批次 |

> **注意**：eventId 对用户不可见，用户不会通过 eventId 来指定事件。eventId 仅在内部用于调用详情 API。

### 执行逻辑

#### 步骤 1：定位 eventId

1. **读取推送历史**：读取 `state/push_history.json`。
2. **根据用户意图匹配事件**：
   - **按序号**（如"第3条"/"第三条详细看看"）：取最近一个 batch 中 `index == 3` 的事件。
   - **按标题/摘要关键词**（如"关于芯片的"/"长飞光纤那条"）：在最近 batch 的 `compliantTitle` 、`summary`和 `original_summary` 中做模糊匹配。如果匹配到多条，列出候选（带序号）让用户选择。
   - **指定历史批次**（如"上一轮第2条"）：在 `batches[1]`（上一轮）中按序号查找。
3. **如果找不到**：提示用户"未在最近的推送记录中找到该事件"，并列出最近一批事件的标题供用户确认。
4. **提取 eventId 和对应的 keyword**（每个 batch 记录了当时的 keyword，eventId 在缓存中但不展示给用户）。

#### 步骤 2：调用详情接口

```bash
cd "$SKILL_DIR"
$PY -c "
import json, sys
sys.stdout.reconfigure(encoding='utf-8')
from event_query import get_event_detail
detail = get_event_detail(keyword=\"\"\"<KEYWORD>\"\"\", event_id='<EVENT_ID>')
print(json.dumps(detail, ensure_ascii=False, indent=2))
"
```

#### 步骤 3：格式化输出 — 结构化事件分析报告

将 API 返回的原始 JSON 组织成以下专业分析报告。报告分为 **8 个板块**，按固定顺序输出，每个板块之间用分隔线隔开。如果某个字段为空或 null，该板块标注"暂无数据"，不省略板块本身。

---

**完整报告模板：**

> **格式说明**：以下模板为理想输出格式。若 LLM 无法精确对齐 box-drawing 字符（如虚线卡片框、方框传导链路），请使用各板块中标注的简化格式，优先保证内容完整和可读性。

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋  事 件 深 度 分 析 报 告
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  📰  compliantTitle
  🕐  eventPublishDate
  🔶  信号等级: signalLevel ｜ 信号类型: signalCategory



═══════════════════════════════════════════════
  📝  一、核 心 摘 要
═══════════════════════════════════════════════


  original_summary 的完整内容



═══════════════════════════════════════════════
  🔗  二、投 资 逻 辑 与 多 空 博 弈
═══════════════════════════════════════════════


  ◆ 核心逻辑链路

    （从 investmentLogic 中提取"核心逻辑："后的内容）

  ◆ 分支逻辑

    · AI多模态布局:
      持股PixVerse → 影视大模型领先 → 商业化落地 → 股权增值

    · 估值重估逻辑:
      PB低于行业 → AI资产未充分定价 → 重估空间明确

    （每条分支单独一段，链路用 → 串联，换行缩进保持整洁）

  ◆ 多空博弈

    🔴 红方 59%  vs  🔵 蓝方 41%  ── 红方微弱占优

    （从 investmentLogic 中提取红蓝方胜率）



═══════════════════════════════════════════════
  🌳  三、推 理 验 证 链 路
═══════════════════════════════════════════════


  ① 步骤1的结论
       ↓
  ② 步骤2的结论
       ↓
  ③ 步骤3的结论
       ↓
  ④ ...
       ↓
  ⑤ 决策综合

  （数据来自 overallReasoningChain，是 JSON 字符串数组，
    解析后逐步展示，用带编号的纵向箭头链串联，每步独占一行）



═══════════════════════════════════════════════
  🏗️  四、产 业 链 全 景
═══════════════════════════════════════════════


  （直接输出 formatted_tree 的完整内容，保留原始缩进和层级结构，
    除此之外不要自己生成其他内容）



═══════════════════════════════════════════════
  ⚡  五、传 导 路 径 与 节 奏
═══════════════════════════════════════════════


  ▸ 触发事件:  transmission_logic.trigger

  ▸ 传导链路:

    节点A ──(周期/B级)──▸ 节点B ──(周期/B级)──▸ 节点C

    （数据来自 transmission_logic.steps 数组，
      每个 step 包含 node / cycle / grade。
      默认使用上方简化箭头格式，LLM 可稳定复现。
      若输出环境支持等宽字符且能精确对齐，可选用方框增强版：

      ┌─────────┐   周期    ┌─────────┐   周期    ┌─────────┐
      │  节点A  │ ────────▸ │  节点B  │ ────────▸ │  节点C  │
      │  B级    │           │  B级    │           │  C级    │
      └─────────┘           └─────────┘           └─────────┘
    ）

  ▸ 总传导周期:  transmission_logic.total_cycle



═══════════════════════════════════════════════
  📚  六、逻 辑 库 匹 配
═══════════════════════════════════════════════


  对本事件提取的可复用投资逻辑模式：

  ── ▸ 逻辑模式 1:  logic_name ──────────────

    📎 标准链路:
       standard_chain
       （如果链路较长，按 → 分段换行展示）

    📊 置信度:   ████████░░  confidence_score × 100%

    📈 历史成功率: ██████░░░░  estimated_success_rate × 100%
       （用进度条可视化：每10%一个█，不足部分用░，总长度固定10格）

    🔍 信号特征:
       signal_characteristics
       （如果是一大段文本，按句号/分号拆分为要点列表：
         · 特征要点 1
         · 特征要点 2
         · 特征要点 3
       ）

    🎯 适用场景:
       · applicable_scenarios[0]
       · applicable_scenarios[1]

    ⚠️ 风险打破条件:
       · risk_breakers[0]
       · risk_breakers[1]


  ── ▸ 逻辑模式 2:  ... ────────────────────
    （同上格式）

  （默认使用上方 ── ▸ 标题 ── 分隔线格式，LLM 可稳定复现。
    若输出环境支持等宽字符，可选用虚线卡片框增强视觉效果：
    ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
      ▸ 逻辑模式 N:  名称
    └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
  ）


  ──────────────────────────────────────
  📋 综合评估:
     extraction_summary
     （如果是长段落，按语义拆分为 2-3 个要点）
  ──────────────────────────────────────

  （数据来自 logic_library_output.extracted_patterns 数组）



═══════════════════════════════════════════════
  🎯  七、核 心 标 的
═══════════════════════════════════════════════


  | 标的名称 | 代码 | 相关度 | 研究观点 |
  |---------|------|-------|---------|
  | 电广传媒 | 000917.SZ | 高 | 中性偏谨慎 |
  | ...     | ...  | ...   | ...     |

  （数据来自 investmentTargetsSummary 数组，每行一个标的。
    必须使用 Markdown 表格格式输出，不要用空格对齐。）



═══════════════════════════════════════════════
  ⚠️  八、关 键 风 险
═══════════════════════════════════════════════


  🔸 1. keyRisks[0]

  🔸 2. keyRisks[1]

  🔸 3. ...

  （每条风险独占一段，风险之间留空行，便于阅读）



═══════════════════════════════════════════════
  🔬  附 录：历 史 案 例 参 考
═══════════════════════════════════════════════


  historical_cases_analysis 的完整内容
  （如果内容是长段落，按案例/时间点拆分为独立段落，
    每个案例之间留空行）


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**字段到板块的完整映射：**

| 板块                  | 数据来源字段                  | 处理说明                                        |
|-----------------------|-------------------------------|-------------------------------------------------|
| 基本信息（报告头）     | `compliantTitle`, `eventPublishDate`, `signalLevel`, `signalCategory` | 直接输出                       |
| 一、核心摘要           | `original_summary`            | 完整输出，不截断                                |
| 二、投资逻辑与多空博弈 | `investmentLogic`             | 拆分为核心逻辑、分支逻辑、多空博弈三段           |
| 三、推理验证链路       | `overallReasoningChain`       | JSON 字符串，需先 `json.loads()` 解析为数组      |
| 四、产业链全景         | `formatted_tree`              | 原样输出，保留树形缩进                           |
| 五、传导路径与节奏     | `transmission_logic`          | 对象，含 `trigger`, `steps[]`, `total_cycle`     |
| 六、逻辑库匹配         | `logic_library_output`        | 含 `extracted_patterns[]` 和 `extraction_summary` |
| 七、核心标的           | `investmentTargetsSummary`    | 数组，每项取 `target_name`, `target_code`, `relevance`, `research_opinion`，必须用 Markdown 表格输出 |
| 八、关键风险           | `keyRisks`                    | 字符串数组，编号列出                             |
| 附录：历史案例参考     | `historical_cases_analysis`   | 完整输出                                         |

**格式规则：**

- 使用 `━` 作报告外边框，`═` 作板块标题分隔线，视觉层次分明
- 板块标题用**中文序号 + 间隔空格**（如"一、核 心 摘 要"），字间加空格使标题更醒目
- 板块标题与内容之间**留两个空行**，内容与下一个板块之间**留三个空行**，保证呼吸感
- 数值型字段（置信度、成功率）转为百分比显示，并用 `█░` 进度条可视化（总长度固定 10 格，每格代表 10%）
- `investmentLogic` 是一段长文本，需要智能拆分为核心逻辑、分支链路、多空博弈三个子段落
- `overallReasoningChain` 是 JSON 字符串格式的数组，必须先解析再用**纵向带编号箭头链**展示
- **长段落拆分规则**：任何超过 3 句话的连续段落，都应按语义拆分为要点列表（用 `·` 前缀），每个要点独占一行。这尤其适用于第六部分的 `signal_characteristics`、`extraction_summary`，以及附录的 `historical_cases_analysis`
- 第六部分每个逻辑模式默认用 `── ▸ 标题 ──` 分隔线标识，保证 LLM 稳定复现；若输出环境支持等宽字符对齐，可选用虚线框 `┌ ─ ─ ┐` / `└ ─ ─ ┘` 增强卡片效果

#### 步骤 4：后续引导

报告输出后追加引导：
- "对报告中的某个板块想深入讨论？直接说'第三部分投资逻辑展开说说'即可。"
- "还想看其他推送事件？告诉我序号或标题关键词。"

---

## 配置文件格式

`state/push_config.json` 结构：

```json
{
  "active": false,
  "interval_minutes": 5,
  "keyword": "AI",
  "page_size": 10,
  "last_push_time": null,
  "daily_summary_active": false
}
```

| 字段                    | 类型    | 说明                                              |
|-------------------------|---------|---------------------------------------------------|
| `active`                | bool    | 推送是否正在运行                                   |
| `interval_minutes`      | int     | 推送间隔，单位分钟，默认 5                         |
| `keyword`               | string  | 语义检索关键词，默认 "AI"                          |
| `page_size`             | int     | 每次推送返回的事件条数，默认 10                     |
| `last_push_time`        | string  | 上次推送时间 (ISO 格式)，初始为 null               |
| `daily_summary_active`  | bool    | 每日统计是否正在运行，随 active 一起启停            |

---

## 触发关键词

以下关键词/意图会触发本技能：

**启动推送**：开始推送、启动推送、事件推送、定时推送、开始监控事件、打开推送等

**手动查询**：查最近X小时/分钟事件、最近有什么事件、查事件、搜事件、看看事件、有什么新事件等

**修改参数**：推送间隔、改为X分钟、推送频率、关注XX（修改关键词）等

**停止推送**：停止推送、关闭推送、暂停推送、取消推送等

**查看详情**：第X条详细看看、详情+序号、那个关于XX的事件、XX那条、上一轮第X条等

---

## 总规则

- **时区统一为北京时间（UTC+8）**：所有写入配置文件和推送历史的时间戳必须使用北京时间，格式示例 `2026-04-09T14:01:30+08:00`。严禁使用 UTC 时间或带 `Z` 后缀的时间戳。`datetime.now()` 在系统时区为 `Asia/Shanghai` 或 `Asia/Beijing` 时已返回北京时间，直接使用即可。
- 全程使用中文输出。
- **定时推送和每日统计在无新事件时必须静默跳过**，不向用户发送任何消息（包括"暂无新事件"提示）。只有手动查询才会展示"暂无新事件"的提示。
- 推送内容保持简洁，突出标题、信号等级和摘要，方便用户快速浏览。
- 详情查询时完整展示所有返回字段，需要映射成中文呈现，帮助用户做深入研判。
- 如果 API 调用失败，直接告知用户"事件数据获取失败"及错误原因，不编造数据。
- 间隔修改等用户偏好必须持久化到 `state/push_config.json`，不依赖会话记忆。
- 同时在 `memory/YYYY-MM-DD.md` 中记录重要的配置变更，便于跨会话回溯。
