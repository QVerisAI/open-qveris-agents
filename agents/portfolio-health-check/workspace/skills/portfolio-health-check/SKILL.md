---
name: portfolio-health-check
description: 串联投资组合快速诊断、深度诊断和优化处方三个子技能。支持对话模式（交互式阶段确认）和 API 模式（无状态一次性执行）。
---

# Portfolio Health Check Workflow

## 角色

这是总控技能，不负责单独输出完整分析结论。它只负责：

- 收集用户输入
- 决定先跑哪个子技能
- 串联三个子技能完成完整的组合分析流程

## 调用模式

### 对话模式（conversation）

用于交互式场景（用户在聊天窗口中逐步输入持仓和参数）：

- 每一阶段结束后询问用户是否继续下一阶段
- 如果用户明确拒绝继续，就停止在当前阶段
- 如果信息不足，先追问完成当前阶段所需的最少信息
- 最多进行 3 轮补充信息对话

### API 模式（api）

用于程序化调用（payload 完整传入，无需追问）：

- 不追问，不确认，直接执行
- 各阶段通过独立 endpoint 调用，不串联
- `/api/v1/portfolio/deep-diagnosis` -> `run_pipeline()`
- `/api/v1/portfolio/optimization` -> `prescription_main.run_optimization()`

## 子技能

- `portfolio-quick-diagnosis/`：快速诊断，负责持仓概览、集中度检查和总体评价。
- `portfolio-deep-diagnosis/`：深度诊断，调用 `run_pipeline()` 完成量化计算。
- `portfolio-optimization/`：优化处方，调用 `prescription_main.py` 完成约束优化。

## 总规则

- 全程使用中文。
- 不要编造实时数据、最新持仓、精确市值或任何未提供、未验证的外部事实。
- 不要给出具体买卖指令。
- 始终区分"已知信息"和"基于假设的信息"。
- 默认不要生成 HTML、PDF 或其他本地报告文件。优先把每阶段结论整理成结构化中文文字，交给 OpenClaw 继续转述给客户。

## 数据来源原则

- **第 1 阶段**必须先调用 QVeris 做标的识别（`identify` 命令），至少补齐股票名称/代码互查、公司简介和行业简介。如 QVeris 无法返回结果，再用公开网络检索补全基础背景。
- **第 2 阶段**的数据拉取由 `run_pipeline()` 内部自动完成（QVeris + THS），不需要手动调用。
- **第 3 阶段**的数据全部来自 Phase 2 输出的 `_internal` 字段，不需要手动拉取。
- 不要自己编造任何数值型结果；拿不到数据时，要明确写"暂无充分证据"。

## QVeris 工具（仅第 1 阶段使用）

```bash
cd ~/.openclaw/workspace/skills/portfolio-health-check && python scripts/portfolio-health-check/qveris_client.py identify "贵州茅台"
cd ~/.openclaw/workspace/skills/portfolio-health-check && python scripts/portfolio-health-check/qveris_client.py identify "贵州茅台" "中国平安" "沪深300ETF"
cd ~/.openclaw/workspace/skills/portfolio-health-check && python scripts/portfolio-health-check/qveris_client.py state show
```

- 第 1 阶段只要开始标的识别，就必须至少调用一次 `identify`。
- `identify` 结果自动写入 `state/portfolio_state.json` 的 `stage1`。

### QVeris 调用规则（必须遵守）

1. **直接执行上面的命令，不要做任何额外操作。** API Key 已通过环境变量预配置，不需要你检查、设置、传递或确认。
2. **禁止**：读 qveris_client.py 源码来"理解"如何调用、用 python -c 内联调用、设置或覆盖 QVERIS_TOKEN 环境变量、检查 API Key 是否存在。
3. **如果命令返回错误**：直接把错误信息告诉用户，说明"QVeris 数据获取失败"。不要 fallback 到 web_search 或自己编造数据。
4. **禁止编造数据**：任何数值型分析结果（波动率、回撤、夏普比率、相关性等）必须来自 `run_pipeline()` 或 `run_optimization()` 的实际计算输出。你不具备估算这些数值的能力，不要尝试。

## 信息收集总则

每次收集信息都遵守这三条：

1. 先收集"完成当前阶段必须有"的信息，不提前问下一阶段内容。
2. 把同一轮需要问的问题合并成一条，避免碎片化追问。
3. 能从用户原文直接识别的内容不要重复确认，只有缺失项才问。

如果用户一次给了很多信息，先整理成结构化输入，再进入下一步。

## 阶段一需要收集的信息

阶段一的目标是把持仓信息整理清楚并完成确认表，所以必须尽量收齐以下内容：

- 持仓清单
- 每个持仓的资产名称
- 每个持仓可能的证券代码
- 每个持仓的输入形式：金额、比例、股数、仅名称
- 每个持仓对应的仓位或金额
- 现金占比
- 是否存在"剩余未动""货币基金""活期理财"等现金线索
- 哪些标的暂时无法确认

如果用户给出的持仓比例加总不足 100%，不能假设剩余部分是现金。必须追问用户：剩余部分是现金、货币基金，还是有其他持仓没有列出。只有在用户明确回答后，才能确定剩余部分的归属。

如果用户只给名称没给仓位，优先追问各标的大概占比或金额。
如果用户只给股数，优先追问大致市值或占比。
如果代码不清楚，优先追问标的全称或交易所代码。

阶段一结束时，必须得到一份可供确认的持仓表。

## 阶段二需要收集的信息

阶段二只在快速诊断完成后进行，目标是补齐深度诊断参数。

必须收集：

- 换仓频率（`rebalance_frequency`）：日内 / 每周 / 每月 / 每季 / 长期持有
- 仓位管理方式（`position_style`）：择时 / 轮动 / 恒定比例 / 定投 / 核心-卫星
- 风险承受度（`risk_tolerance`）：保守 / 稳健 / 积极 / 激进
- 投资期限（`investment_horizon`）：<1年 / 1-3年 / 3-5年 / >5年

如果用户不理解这些项，优先用选项形式提问，不要让用户自由发挥太多。

## 阶段三需要收集的信息

阶段三只在深度诊断完成后进行，目标是收集优化约束。

必须收集：

- 可投资范围（`allowed_markets`）：A股 / 港股通 / 美股 等
- 可使用工具（`allowed_instruments`）：股票 / ETF / 期货 / 期权 等
- 还能投入多少资金（`additional_capital_ratio`）：无 / 10-30% / 30-50% / 50%+
- 这次优化的目标（`objectives`）：资产增值 / 稳定现金流 / 对冲风险 / 打新底仓

如果用户不理解这些项，先让用户从几个常见目标里选，不要让用户用长段文字自由描述。

## 股票代码处理规则

如果用户只给了股票名称、ETF 名称或简称，没有给代码，可以先自己检索公开信息补全代码。

1. 优先根据名称识别常见代码。
2. 如果名称可能对应多个标的，先标记为待确认，再追问用户。
3. 如果能高置信度识别，就直接补上代码，不要强迫用户自己补。
4. 只有在名称歧义较大或无法确认时，才把代码标为"无法确认"。

## 强制流程（对话模式）

### 第 1 阶段：快速诊断

1. 收集持仓、现金占比和必要的基础信息。
2. 调用 `portfolio-quick-diagnosis/`。
3. 先输出名称/代码互查表和公司/行业简介，再输出持仓确认表与结构化快速诊断结果。
4. 询问用户是否继续进行深度诊断。

### 第 2 阶段：深度诊断

仅在第 1 阶段完成且用户明确同意后进行。

1. 补充深度诊断所需参数。
2. 调用 `portfolio-deep-diagnosis/`（内部通过 `run_pipeline()` 自动完成数据拉取和量化计算）。
3. 输出深度分析的结构化结果。
4. 询问用户是否继续进行优化处方。

### 第 3 阶段：优化处方

仅在第 2 阶段完成且用户明确同意后进行。

1. 补充约束条件。
2. 调用 `portfolio-optimization/`（内部通过 `prescription_main.py` 完成优化计算）。
3. 输出结构化优化建议。

## 顺序约束

- 必须先完成第 1 阶段，才能进入第 2 阶段。
- 必须先完成第 2 阶段，才能进入第 3 阶段。
- 不允许跳过阶段。
- 不允许并行执行阶段。
- 对话模式下，每一阶段结束后都必须询问是否继续下一阶段。

## 参考

- `scripts/portfolio-health-check/qveris_client.py`
- `portfolio-quick-diagnosis/SKILL.md`
- `portfolio-deep-diagnosis/SKILL.md`
- `portfolio-optimization/SKILL.md`
