---
name: portfolio-deep-diagnosis
description: 用于对投资组合进行深度诊断。收集 4 个分析参数后调用 run_pipeline() 完成量化计算，再向用户解释诊断结果。
---

# 投资组合深度诊断

## 目的

把持仓与分析参数整理成结构化的深度诊断结果，重点输出相关性、风险指标、因子暴露和关键风险点。

## 适用场景

- 进一步分析组合的波动、相关性和风险结构
- 结合参数判断持仓管理风格
- 识别组合中的风格暴露和因子偏向
- 输出量化/技术视角的诊断结论，并整理成可直接转述给客户的结构化文字

## 路径约定

`SKILL_ROOT` = 本 SKILL.md 的**父目录**（即 `portfolio-health-check/` 技能根目录）。所有 `cd "$SKILL_ROOT"` 命令均指向技能根目录，不要硬编码绝对路径。

## 前提条件

- 第 1 阶段（快速诊断）已完成，持仓确认表已定稿
- `state/portfolio_state.json` 的 `stage1` 已有结构化持仓数据

## 核心规则

- 全程使用中文。
- 不要声称自己知道实时价格、最新基金持仓，或任何未提供、未验证的外部事实。
- 不要给出买卖指令，也不要预测市场走势。
- 清楚区分已知事实和粗略假设。
- 最多进行 3 轮补充信息对话。
- 默认不要生成 HTML、PDF 或其他本地报告文件。优先返回 `client_output` 结构化文字给父级 workflow 或 OpenClaw。

## 执行流程

### Step 1：收集深度诊断参数

必须收齐 4 个参数，缺一不可：

| 参数 | 字段名 | 选项 |
|------|--------|------|
| 换仓频率 | `rebalance_frequency` | `intraday` / `weekly` / `monthly` / `quarterly` / `buy_and_hold` |
| 仓位风格 | `position_style` | `market_timing` / `full_rotation` / `constant_mix` / `dca` / `core_satellite` |
| 风险偏好 | `risk_tolerance` | `conservative` / `moderate` / `aggressive` / `very_aggressive` |
| 投资期限 | `investment_horizon` | `<1y` / `1-3y` / `3-5y` / `>5y` |

如果用户不理解某个参数，用选项形式提问，不要让用户自由描述。

### Step 2：组装 payload 并通过 CLI 调用 pipeline

用第 1 阶段的持仓数据和本阶段收集的参数构建 payload JSON 文件，然后通过命令行调用 pipeline。

**Step 2a：写 payload 文件**

用 exec 工具把 payload 写到 `/tmp/portfolio_payload.json`：

```json
{
    "holdings": [
        {"code": "600519.SH", "name": "贵州茅台", "weight_pct": 30.0},
        {"code": "300750.SZ", "name": "宁德时代", "weight_pct": 25.0}
    ],
    "cash_pct": 10.0,
    "params": {
        "rebalance_frequency": "monthly",
        "position_style": "core_satellite",
        "risk_tolerance": "moderate",
        "investment_horizon": "3-5y",
        "portfolio_market_value": 1000000
    }
}
```

> 从 `state/portfolio_state.json` 的 `stage1` 读取持仓数据构建 holdings 数组。每个 holding 必须有 `code` 和 `weight_pct`。

**Step 2b：调用 CLI**

```bash
cd "$SKILL_ROOT" && \
  python scripts/portfolio-health-check/pipeline_main.py \
  /tmp/portfolio_payload.json \
  --emit-artifacts \
  --output-dir /tmp/portfolio_output \
  --as-of $(date +%Y-%m-%d)
```

> 执行成功后，结果文件在 `/tmp/portfolio_output/` 目录：
> - `diagnosis_result.json` — 完整诊断结果（Step 3 解读用）
> - `_internal.json` — 内部数据（Phase 3 优化用，不需要解读）
> - `diagnosis_report.html` — HTML 报告（仅在用户要求时提及）

**Step 2c：读取结果**

```bash
cat /tmp/portfolio_output/diagnosis_result.json
```

**`pipeline_main.py` CLI 内部自动完成：**
- QVeris 数据拉取（行情、基本面、市值、基准）
- 按 `rebalance_frequency` 选择数据粒度和回看窗口
- 15 步量化诊断（相关性、风险指标、集中度、因子暴露、流动性、风险旗标等）
- 诊断结果整理成 `client_output` 结构化文字

**不要手动调用 QVeris/THS 取数据，不要手动跑分析逻辑。** CLI 是唯一计算入口。

### Step 3：处理返回结果

`run_pipeline()` 返回 `dict`，结构如下：

```python
# 成功
{
    "status": "ok",
    "error_message": None,
    "data": {
        "correlation_matrix": {...},
        "risk_metrics": {"holdings": [...], "portfolio": {...}},
        "risk_contribution": {...},
        "concentration": {...},
        "benchmark": {...},
        "factor_exposure": {...},
        "sector_exposure": {...},
        "liquidity": {...},
        "intraday_micro": {...},    # 仅 intraday 频率
        "risk_flags": [...],
        "metadata": {...},
    },
    "client_output": {
        "title": "组合诊断摘要",
        "headline": "...",
        "sections": [...],
        "markdown": "..."
    },
    "artifacts": null,              # 默认不生成文件；显式要求时才有路径
    "pipeline": {
        "as_of": "2026-04-01",
        "requested_rebalance_frequency": "monthly",
        "selected_benchmark": {...},
        "qveris_inputs": {...},
    },
}

# 失败
{
    "status": "error",
    "error_message": "具体错误信息",
    "data": None,
    "artifacts": None,
}
```

### Step 4：向用户解释诊断结果

**如果 `status == "ok"`：**

1. 先用 1-2 句话给出总体判断（组合更像什么类型、核心风险在哪）。
2. 优先使用 `client_output` 里的 `headline / sections / markdown` 作为对客交付底稿。
3. 如需补充解释，再按以下顺序解读底层 section：
   - `risk_flags`：哪些旗标被触发，严重程度如何
   - `risk_metrics.portfolio`：波动率、夏普、最大回撤等关键指标
   - `concentration`：集中度是否过高
   - `correlation_matrix.high_correlation_pairs`：高相关性配对
   - `factor_exposure.portfolio`：因子偏向
   - `liquidity`：清仓所需天数
   - `benchmark`：与基准的相对表现
4. 不要把原始 JSON 贴给用户，要翻译成人能读的结论。
5. 对无法确认的地方标注为推断。
6. 只有在用户明确要求导出报告时，才说明 `artifacts` 里的文件路径。

**如果 `status == "error"`：**

1. 告知用户诊断未能完成。
2. 根据 `error_message` 判断是数据问题（QVeris 不可达、代码不匹配）还是代码 bug。
3. 如果是数据问题，建议用户检查持仓代码或稍后重试。
4. 如果是代码 bug，如实告知。

### Step 5：交还控制权

诊断结果解释完毕后，把控制权交回父级 workflow（`portfolio-health-check/SKILL.md`），由父级询问用户是否继续进入第 3 阶段。

## 边界规则

- 不得编造历史收益、波动率、回撤、相关系数或因子暴露数值——这些全部由 `run_pipeline()` 计算得出。
- 如果 `run_pipeline()` 返回的某些指标因数据不足而缺失（见 `metadata.warnings`），要如实告知用户。
- 可以做基于诊断结果的定性判断，但必须标注判断依据来自哪个 section。

## 参考文件

- [pipeline_main.py](../scripts/portfolio-health-check/pipeline_main.py) — Phase 2 唯一计算入口
- [diagnosis.py](../scripts/portfolio-health-check/diagnosis.py) — 15 步诊断编排
