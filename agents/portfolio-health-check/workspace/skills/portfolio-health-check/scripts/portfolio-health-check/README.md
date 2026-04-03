# Portfolio Health Check

A-share 组合健康诊断引擎。输入持仓和场景参数，默认输出 15 步量化诊断结果 + `client_output` 结构化文字；HTML/PDF 仅在显式要求时生成。

## 架构

```
pipeline_main.py          API 入口：归一化 payload → 拉取行情 → 诊断 → 返回结构化文字（可选 artifacts）
  ├─ qveris_client.py     QVeris API 客户端（行情/基本面数据）
  ├─ diagnosis.py          诊断编排器：串联 15 个步骤
  │   ├─ data_loader.py    数据加载 & 格式归一化
  │   └─ compute/          纯计算模块
  │       ├─ returns.py         收益率（constant_mix / drift / periodic rebalance / DCA）
  │       ├─ correlation.py     相关性矩阵 & 高相关标记
  │       ├─ risk_metrics.py    12 项风险指标（Sharpe / Sortino / Calmar / VaR / CVaR …）
  │       ├─ risk_contribution.py  边际/百分比风险贡献 + 行业聚合
  │       ├─ concentration.py   HHI / Effective N / 集中度
  │       ├─ benchmark.py       基准选择（沪深300/中证500）+ 相对指标
  │       ├─ factor_engine.py   6 因子打分（size/value/momentum/quality/vol/liquidity）
  │       ├─ factor_exposure.py 组合因子暴露
  │       ├─ liquidity.py       清仓天数估算
  │       ├─ risk_flags.py      7 项风险旗标 + 阈值体系
  │       ├─ style_adjuster.py  投资风格后处理
  │       └─ thresholds.py      4 档风险阈值 + 参数合法性校验
  ├─ generate_report.py         PDF 报告生成
  ├─ generate_report_html.py    HTML 报告生成
  └─ date_utils.py              共享日期工具函数
```

## 依赖

### Python 包

| 包 | 用途 | 必需 |
|----|------|------|
| `pandas` | 数据加载、对齐、分组聚合 | 是 |
| `numpy` | 数值计算（协方差、年化、VaR 等） | 是 |
| `matplotlib` | PDF 报告生成（PdfPages） | 仅 PDF 报告 |

### 测试

| 包 | 用途 |
|----|------|
| `pytest` | 测试框架 |

### 外部工具

| 工具 | 用途 | 必需 |
|------|------|------|
| Chrome / Edge | HTML → PDF 转换（headless 模式） | 仅 `--pdf` 选项 |

### 安装

```bash
pip install pandas numpy matplotlib pytest
```

## 快速开始

### 1. 环境准备

```bash
# 激活虚拟环境
source ../../.venv/Scripts/activate  # Git Bash
# 或
..\..\..\.venv\Scripts\activate      # PowerShell

# 配置 QVeris API Key（从 .env 文件读取或手动设置）
export QVERIS_TOKEN="your_api_key_here"
```

> API Key 存放在 `.env` 文件中，已被 `.gitignore` 排除，不会进入版本控制。

### 2. 命令行运行

```bash
python pipeline_main.py payload.json --as-of 2026-03-31
python pipeline_main.py payload.json --as-of 2026-03-31 --emit-artifacts
python pipeline_main.py payload.json --as-of 2026-03-31 --pdf
```

默认情况下，`run_pipeline()` 和 CLI 都返回结构化的 `client_output`，不会额外落本地文件。
只有在以下情况才会生成文件产物：

- 显式传入 `output_dir`
- 显式传入 `emit_artifacts=True` 或 `--emit-artifacts`
- 显式要求 `include_pdf=True` 或 `--pdf`

`payload.json` 格式：

```json
{
  "holdings": [
    {"code": "600519.SH", "weight_pct": 30.0, "name": "贵州茅台"},
    {"code": "300750.SZ", "weight_pct": 25.0, "name": "宁德时代"},
    {"code": "510500.SH", "weight_pct": 20.0, "name": "中证500ETF"},
    {"code": "600036.SH", "weight_pct": 10.0, "name": "招商银行"}
  ],
  "cash_pct": 15.0,
  "params": {
    "rebalance_frequency": "monthly",
    "position_style": "core_satellite",
    "risk_tolerance": "moderate",
    "investment_horizon": "3-5y",
    "portfolio_market_value": 5000000
  }
}
```

### 3. 编程调用

```python
from diagnosis import run_diagnosis

result = run_diagnosis(
    scenario_input={
        "trading_frequency": "monthly",
        "data_frequency": "daily",
        "lookback_period": "2y",
        "position_style": "core_satellite",
        "risk_tolerance": "moderate",
        "investment_horizon": "3-5y",
        "portfolio_market_value": 5_000_000,
    },
    portfolio_input={
        "holdings": [
            {"code": "600519.SH", "weight_pct": 30.0, "sector_theme": "consumer-staples"},
            {"code": "300750.SZ", "weight_pct": 25.0, "sector_theme": "new-energy"},
        ],
        "cash_pct": 15.0,
    },
    prices_input=prices_df,          # DataFrame 或 {"main": df, "daily": df}
    fundamentals_input=fundamentals_df,
    benchmark_input=benchmark_df,
)
```

所有输入均支持 CSV 路径、DataFrame、或 records list。

## 诊断 15 步流程

| 步骤 | 内容 | 输出 key |
|------|------|---------|
| 1 | 加载场景参数 | metadata |
| 2 | 加载持仓 & 权重归一化 | — |
| 3 | 参数合法性校验 | metadata.warnings |
| 4 | 加载行情/基本面数据 | — |
| 5 | 按 lookback 截断 | — |
| 6 | 收益率计算 | risk_metrics |
| 7 | 相关性矩阵 | correlation_matrix |
| 8 | 12 项风险指标 | risk_metrics |
| 9 | 风险贡献 | risk_contribution |
| 10 | 集中度 | concentration |
| 11 | 基准对比 | benchmark |
| 12 | 6 因子打分 | factor_exposure |
| 13 | 行业暴露 | sector_exposure |
| 14 | 流动性 | liquidity |
| 15 | 风险旗标 + 风格调整 | risk_flags |

## 参数说明

### 场景参数

| 参数 | 可选值 | 说明 |
|------|--------|------|
| `rebalance_frequency` | intraday / weekly / monthly / quarterly / buy_and_hold | 调仓频率 |
| `position_style` | timing_rotation / full_rotation / constant_mix / dca / core_satellite | 投资风格 |
| `risk_tolerance` | conservative / moderate / aggressive / very_aggressive | 风险承受等级 |
| `investment_horizon` | <1y / 1-3y / 3-5y / >5y | 投资期限 |
| `portfolio_market_value` | float (CNY) | 组合总市值（用于流动性分析） |

### 风险阈值体系

阈值按 `risk_tolerance` 分 4 档，`investment_horizon` 对波动率和回撤阈值施加乘数调整：

- `<1y` → 0.8x（更严格）
- `1-3y` → 1.0x
- `3-5y` → 1.2x
- `>5y` → 1.5x（更宽松）

## QVeris 客户端

`qveris_client.py` 是独立的 QVeris API 命令行工具：

```bash
# 搜索工具
python qveris_client.py search "stock historical price"

# 收集行情数据
python qveris_client.py ths-collect "600519.SH,000001.SZ" --rebalance-frequency weekly

# 导出 CSV
python qveris_client.py export-csv --output close_volume.csv
```

详细用法见 `python qveris_client.py --help`。

## 测试

```bash
cd skills/portfolio-health-check
python -m pytest tests/ -v
```

179 个测试覆盖：单元测试（各 compute 模块）、集成测试（端到端诊断）、输出契约测试（精度/字段规范）。

## 输出结构

```json
{
  "status": "ok",
  "data": {
    "correlation_matrix": { "labels": [], "matrix": [], "high_correlation_pairs": [] },
    "risk_metrics": { "holdings": [], "portfolio": {} },
    "risk_contribution": { "by_holding": [], "by_sector": [] },
    "concentration": { "hhi": 0.0, "effective_n": 0.0, "top_3_pct": 0.0 },
    "benchmark": { "benchmark_code": "", "beta": 0.0, "alpha_annual": 0.0 },
    "factor_exposure": { "factor_order": [], "holdings": {}, "portfolio": {} },
    "sector_exposure": {},
    "liquidity": { "holdings": [], "portfolio_max_liquidation_days": 0.0 },
    "risk_flags": [{ "severity": "", "metric": "", "explanation": "" }],
    "metadata": { "data_frequency": "", "warnings": [] }
  },
  "client_output": {
    "title": "组合诊断摘要",
    "headline": "给客户看的总判断",
    "sections": [
      { "heading": "总体判断", "bullets": ["..."] }
    ],
    "markdown": "# 组合诊断摘要\n..."
  },
  "artifacts": null
}
```
