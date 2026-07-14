---
name: portfolio-health-check
description: 投资组合健康检查：快速诊断、深度诊断、优化处方三阶段串行完成持仓分析。支持对话模式（交互式阶段确认）和 API 模式（无状态一次性执行）。
---

# 投资组合健康检查

本技能通过三个阶段串行完成一次完整的组合分析：**快速诊断 → 深度诊断 → 优化处方**。三个阶段的执行步骤全部写在本文件内，无需跳转其它 SKILL 文件。

## 调用模式

### 对话模式（conversation）
交互式场景（用户在聊天窗口逐步输入持仓和参数）：
- 每一阶段结束后询问用户是否继续下一阶段
- 用户明确拒绝就停在当前阶段
- 信息不足先追问完成当前阶段所需的最少信息
- 最多 3 轮补充信息对话

### API 模式（api）
程序化调用（payload 完整传入，无需追问）：不追问、不确认、直接执行；各阶段独立调用，不串联。

## 总规则
- 全程中文。
- 不编造实时数据、最新持仓、精确市值或任何未提供/未验证的外部事实。
- 不给具体买卖指令、不预测市场走势。
- 始终区分"已知事实"和"基于假设"。
- 默认不生成 HTML/PDF/本地报告文件，优先把每阶段结论整理成结构化中文文字。
- 任何数值型结果（波动率、回撤、夏普、相关性等）必须来自脚本实际计算输出，不得估算或编造。

## 路径与脚本约定
- 所有可执行逻辑都在 `scripts/portfolio-health-check/` 下的 Python 脚本里（`qveris_client.py` / `pipeline_main.py` / `prescription_main.py`）。
- `SKILL_ROOT` = 本 SKILL.md 所在目录。执行前从 SKILL.md 加载路径推导技能根目录，用 `cd "$SKILL_ROOT"`，不要硬编码绝对路径。
- **只通过 `python scripts/portfolio-health-check/<脚本>.py <参数>` 调用包内 .py 脚本。禁止 `python -c` 内联执行、禁止读脚本源码"理解"调用方式、禁止设置/覆盖/检查 `QVERIS_TOKEN`**（API Key 已由环境变量预配置）。命令返回错误时直接把错误告知用户，不 fallback 到 web_search、不编造数据。

---

## 阶段一：快速诊断

目标：把持仓整理成结构化快速诊断结果——标的识别、持仓确认、集中度检查、总体评价。

### 1. 标的识别（阶段一第一步，必做）
用 QVeris `identify` 命令一次性获取名称/代码互查、公司简介、行业分类：

```bash
cd "$SKILL_ROOT" && python scripts/portfolio-health-check/qveris_client.py identify "贵州茅台"
cd "$SKILL_ROOT" && python scripts/portfolio-health-check/qveris_client.py identify "贵州茅台" "中国平安" "沪深300ETF"
```

- 支持输入：公司名（`"贵州茅台"`）、完整代码（`"600519.SH"`）、纯数字（`"600519"`）、ETF 名（`"沪深300ETF"`）。
- `identify` 按优先级尝试 `hangseng_polysource.stock.basicCorpInfo.retrieve.v2`（主，返回全称/行业分类/主营/概念）→ `ths_ifind.company_basics.v1`（fallback 补 name）。结果自动写入 `state/portfolio_state.json` 的 `stage1`，供后续脚本读取；用户更正后更新同一文件，不另存。
- 返回字段：`code_lookup.codes`（THS 代码列表）、`code_lookup.is_ambiguous`（是否多候选）、`primary_code`、`company`（全称/主营/概念）、`asset_type`（`stock`/`fund_or_etf`）、`industry`（个股才有）。
- 歧义处理：`is_ambiguous=true` → 列候选、标"待确认"；A+H 多市场默认取第一个（通常 A 股）、备注其余；`resolved=false` → 改用 `search` 命令更宽泛搜索；QVeris 找不到再用公开网络补全。

### 2. 信息收集与确认
- 从用户消息提取：资产名称、可能代码、输入形式（金额/比例/股数/仅名称）、现金线索（"现金"/"货币基金"/"剩余"）。
- 金额 → 按总额归一化为比例；只给股数 → 追问大致市值或占比，不凭直觉估权重。
- **比例加总不足 100% 不能默认剩余是现金**，必须追问剩余部分是什么。
- 追问尽量合并成一次，只问缺失项。

### 3. 阶段一输出顺序（先两块，再确认表）
1. **名称/代码互查表**：名称、代码、候选代码、是否待确认（只对名称和代码，不放长简介）。
2. **公司与行业简介**：简短中文说明主营、业务特点、所属行业。
3. **持仓确认表**：

| 资产名称 | 代码 | 占比 | 资产类别 | 备注 |
|---|---|---:|---|---|
| 示例资产 | 600519.SH | 30% | 股票/ETF/债券/现金/其他 | 待确认（如需） |

无法确认的字段写"待确认"/"无法确认"。确认提示示例："请确认以下持仓信息；如有不准确请直接修改名称、仓位或现金占比。"

### 4. 生成分析与报告
用户明确确认持仓后：
- 按 `portfolio-quick-diagnosis/analysis_prompt.md` 生成分析要点（分析逻辑、检索范围、推断边界、风险判断）。
- 按 `portfolio-quick-diagnosis/report_prompt.md` 生成最终结构化中文交付文案。
- 对 ETF 与个股重叠只做保守判断（"可能存在重叠"）；无法识别的标的写"无法确认"。

### 5. 收尾
输出结构化快速诊断结果后，询问用户是否继续深度诊断。

---

## 阶段二：深度诊断

仅在阶段一完成且用户明确同意后进行。目标：量化分析波动率、相关性、回撤、因子暴露、风险贡献。

### 1. 收集 4 个参数（缺一不可）
| 参数 | 字段 | 选项 |
|---|---|---|
| 换仓频率 | `rebalance_frequency` | `intraday`/`weekly`/`monthly`/`quarterly`/`buy_and_hold` |
| 仓位风格 | `position_style` | `market_timing`/`full_rotation`/`constant_mix`/`dca`/`core_satellite` |
| 风险偏好 | `risk_tolerance` | `conservative`/`moderate`/`aggressive`/`very_aggressive` |
| 投资期限 | `investment_horizon` | `<1y`/`1-3y`/`3-5y`/`>5y` |

用户不理解某参数时用选项形式提问。

### 2. 构建 payload 数据文件
从 `state/portfolio_state.json` 的 `stage1` 读持仓，写一个 payload JSON 数据文件到 `/tmp/portfolio_payload.json`，结构：

```json
{
    "holdings": [
        {"code": "600519.SH", "name": "贵州茅台", "weight_pct": 30.0},
        {"code": "300750.SZ", "name": "宁德时代", "weight_pct": 25.0}
    ],
    "cash_pct": 10.0,
    "params": {
        "rebalance_frequency": "monthly", "position_style": "core_satellite",
        "risk_tolerance": "moderate", "investment_horizon": "3-5y",
        "portfolio_market_value": 1000000
    }
}
```

每个 holding 必须有 `code` 和 `weight_pct`。

### 3. 运行深度诊断脚本
```bash
cd "$SKILL_ROOT" && python scripts/portfolio-health-check/pipeline_main.py \
  /tmp/portfolio_payload.json --emit-artifacts \
  --output-dir /tmp/portfolio_output --as-of $(date +%Y-%m-%d)
```

`pipeline_main.py` 内部自动完成：QVeris 取数（行情/基本面/市值/基准）→ 按 `rebalance_frequency` 选数据粒度和回看窗口 → 15 步量化诊断 → 整理成 `client_output`。**不要手动调 QVeris/THS，不要手动跑分析逻辑，CLI 是唯一计算入口。**

### 4. 读取并解读结果
读取 `/tmp/portfolio_output/diagnosis_result.json`。它是 envelope：`status="ok"` 时 `data` 含 `correlation_matrix`/`risk_metrics`/`risk_contribution`/`concentration`/`benchmark`/`factor_exposure`/`sector_exposure`/`liquidity`/`risk_flags`/`metadata`，`client_output` 含 `headline`/`sections`/`markdown`；`_internal.json` 供阶段三使用。

解读（status=ok）：先用 1-2 句给总体判断 → 优先用 `client_output` 作对客底稿 → 需补充再按序读 `risk_flags`（触发旗标+严重度）、`risk_metrics.portfolio`（波动/夏普/回撤）、`concentration`、`correlation_matrix.high_correlation_pairs`、`factor_exposure`、`liquidity`、`benchmark`。**不要贴原始 JSON，翻译成人话；不确定处标注为推断；只有用户明确要报告文件时才提 `artifacts` 路径。**

status=error：告知未完成，按 `error_message` 判断是数据问题（建议查代码/稍后重试）还是代码 bug（如实告知）。

### 5. 收尾
解读完毕，询问用户是否继续优化处方。

---

## 阶段三：优化处方

仅在阶段二完成且用户明确同意后进行。目标：基于约束给出分层优化建议。

### 1. 收集约束
- 可投资范围 `allowed_markets`（A股/港股通/美股…）
- 可用工具 `allowed_instruments`（股票/ETF/期货/期权…）
- 追加资金 `additional_capital_ratio`（无/10-30%/30-50%/50%+）
- 优化目标 `objectives`（资产增值/稳定现金流/对冲风险/打新底仓）

用户不理解时先给常见目标选项，不要让用户长段自由描述。

### 2. 构建约束数据文件
写一个 constraints JSON 数据文件到 `/tmp/portfolio_constraints.json`：

```json
{
    "allowed_markets": ["A-share"],
    "allowed_instruments": ["stock", "etf"],
    "additional_capital_ratio": "10-30%",
    "objectives": ["growth"]
}
```

### 3. 运行优化脚本
```bash
cd "$SKILL_ROOT" && python scripts/portfolio-health-check/prescription_main.py \
  --diagnosis /tmp/portfolio_output/diagnosis_result.json \
  --internal /tmp/portfolio_output/_internal.json \
  --constraints-file /tmp/portfolio_constraints.json \
  --output-dir /tmp/optimization_output
```

`--internal` 指向阶段二输出的 `_internal.json`；缺失可省略（优化结果降级，跳过回验和压力测试）。`prescription_main.py` 内部完成：剥出诊断数据和 `_internal` → 解析约束 → 推理/映射/回验/压力测试 → 返回结构化处方。**阶段三所有数据来自阶段二输出，不要再调 QVeris。**

### 4. 读取并解读结果
读取 `/tmp/optimization_output/optimization_result.json`。`status="ok"` 时 `data` 含 `recommendations`/`exclusive_groups`/`asset_alignment`/`constraints_applied`/`summary`/`client_output`/`execution_info`。优先用 `client_output` 作对客底稿，翻译成人话，只做方向性建议、不给具体买卖指令。

---

## 顺序约束
- 必须按 阶段一 → 阶段二 → 阶段三 顺序，不允许跳过或并行。
- 对话模式下每阶段结束都要询问是否继续下一阶段。

## 参考文件
- `scripts/portfolio-health-check/qveris_client.py` — 阶段一标的识别入口
- `scripts/portfolio-health-check/pipeline_main.py` — 阶段二唯一计算入口
- `scripts/portfolio-health-check/prescription_main.py` — 阶段三唯一计算入口
- `portfolio-quick-diagnosis/analysis_prompt.md`、`portfolio-quick-diagnosis/report_prompt.md` — 阶段一分析/报告 prompt
