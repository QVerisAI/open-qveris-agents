---
name: portfolio-optimization
description: 用于基于约束条件生成投资组合优化处方。调用 prescription_main.py 完成量化优化，仅保留约束收集、异常处理和结果解释。
---

# 投资组合优化处方

## 目的

在持仓现状、深度诊断结果和用户约束条件基础上，生成分层优化建议与前后对比。

## 适用场景

- 生成组合优化建议
- 依据约束条件筛选可行动作空间
- 给出零成本、需额外资金、需新品种三级建议
- 输出优化前后对比的结构化结果，方便直接转述给客户

## 前提条件

- 第 2 阶段（深度诊断）已完成，`run_pipeline()` 的完整输出可用
- 约束条件已从用户处收集

## 核心规则

- 全程使用中文。
- 不要编造实时价格、最新持仓、精确市值或任何未提供、未验证的外部事实。
- 不要给出具体买卖指令。
- 所有建议都必须受用户约束限制。
- 默认不要生成 HTML、PDF 或其他本地报告文件。优先返回结构化中文说明。
- 最多进行 3 轮补充信息对话。

## 约束收集

必须收集以下约束（新格式字段名）：

| 参数 | 字段名 | 选项 |
|------|--------|------|
| 可投资市场 | `allowed_markets` | `A-share` / `HK` / `US` |
| 可接受敞口 | `allowed_exposure` | `A-share` / `HK` / `US` / `global`（默认 = allowed_markets + global）|
| 可使用工具 | `allowed_instruments` | `stock` / `etf` / `fund` / `futures` / `option` / `crypto` |
| 已开通权限 | `account_permissions` | `option_account` / `futures_account` / `hk_connect` / `qdii` |
| 可追加资金 | `additional_capital_ratio` | `none` / `10-30%` / `30-50%` / `50%+` |
| 优化目标 | `objectives` | `growth` / `income` / `hedge` / `ipo_base`（多选）|

> **向后兼容：** 旧格式 `investable_markets` / `available_capital` / `objectives`（旧枚举）仍被接受，`parse_constraints()` 自动转换为新格式。

如果用户不理解这些项，先让用户从几个常见目标里选，不要让用户用长段文字自由描述。

## 执行流程

### Step 1：收集约束

从用户处收集上述约束参数。如果参数不足，先追问最少必要信息。

### Step 2：组装约束并通过 CLI 调用优化

用 Phase 2 的输出文件和约束构建调用。

**Step 2a：写约束文件**

用 exec 工具把约束写到 `/tmp/portfolio_constraints.json`：

```json
{
    "allowed_markets": ["A-share"],
    "allowed_instruments": ["stock", "etf"],
    "additional_capital_ratio": "10-30%",
    "objectives": ["growth"]
}
```

**Step 2b：调用 CLI**

```bash
cd ~/.openclaw/workspace/skills/portfolio-health-check && \
  python scripts/portfolio-health-check/prescription_main.py \
  --diagnosis /tmp/portfolio_output/diagnosis_result.json \
  --internal /tmp/portfolio_output/_internal.json \
  --constraints-file /tmp/portfolio_constraints.json \
  --output-dir /tmp/optimization_output
```

> - `--diagnosis` 和 `--internal` 指向 Phase 2 `--output-dir` 里的文件
> - 如果 `_internal.json` 不存在，可省略 `--internal`（优化结果会降级，跳过回验和压力测试）
> - 执行成功后，`/tmp/optimization_output/optimization_result.json` 包含完整结果

**Step 2c：读取结果**

```bash
cat /tmp/optimization_output/optimization_result.json
```

**`prescription_main.py` CLI 内部自动完成：**
- 从 `diagnosis_result` 剥出诊断数据和 `_internal`（returns/holdings）
- 解析约束
- 运行 Phase 3 推理、映射、回验、压力测试
- 返回结构化处方结果

**不要手动调用 QVeris 或拉取数据。** Phase 3 的所有数据来自 Phase 2 输出。

### Step 3：处理返回结果

`run_optimization()` 返回 envelope：

```python
# 成功
{
    "status": "ok",
    "error_message": null,
    "data": {
        "recommendations": {...},
        "exclusive_groups": [...],
        "asset_alignment": {...},
        "constraints_applied": {...},
        "summary": {...},
        "client_output": {...},
        "execution_info": {"rescore_executed": true, "stress_test_executed": true, "warnings": []}
    }
}

# 失败
{
    "status": "error",
    "error_message": "...",
    "data": null
}
```

### Step 4：向用户解释优化结果

**如果 `status == "ok"`：**

1. 优先使用 `client_output` 里的结构化文字作为对客交付底稿。
2. 按推荐优先级解释 tier 1 / tier 2 / tier 3 建议。
3. 如果 `execution_info.rescore_executed == false`，告知用户推荐未经回验。
4. 不要把原始 JSON 贴给用户。

**如果 `status == "error"`：**

1. 告知用户优化未能完成。
2. 根据 `error_message` 判断是输入问题还是代码 bug。

### Step 5：交还控制权

结果解释完毕后，流程结束。

## 边界规则

- 优化建议只能停留在资产配置和策略层面。
- 如果用户约束太强（`constrained` 状态），允许输出"暂不建议"并说明约束瓶颈。
- 如果需要使用行情/公开资料，只能做方向性判断，不能把不确定内容写死。

## 参考文件

- [prescription_main.py](../scripts/portfolio-health-check/prescription_main.py) - Phase 3 唯一 API 入口
- [prescription.py](../scripts/portfolio-health-check/prescription.py) - Phase 3 引擎
