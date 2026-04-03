"""
Generate magazine-style paged HTML report from diagnosis results.
Open in Chrome → Ctrl+P → Save as PDF (margins: none, background graphics: on).

TODO: Migrate from f-string concatenation to Jinja2 templates for maintainability.
"""

from __future__ import annotations
import sys
import math
from pathlib import Path

from diagnosis import run_diagnosis


def _get_assets():
    from asset_paths import get_assets_dir

    return get_assets_dir(__file__)


ASSETS = None  # lazy-loaded when needed


def pct(v, d=1):
    return f"{v * 100:.{d}f}%" if v is not None else "—"


def num(v, d=2):
    return f"{v:.{d}f}" if v is not None else "—"


def spct(v):
    if v is None:
        return "—"
    return f"{'+' if v > 0 else ''}{v * 100:.1f}%"


def snum(v):
    if v is None:
        return "—"
    return f"{'+' if v > 0 else ''}{v:.2f}"


def cn_tol(t):
    return {
        "conservative": "保守",
        "moderate": "稳健",
        "aggressive": "积极",
        "very_aggressive": "激进",
    }.get(t, t)


def cn_sty(s):
    return {
        "timing_rotation": "择时轮动",
        "full_rotation": "满仓轮动",
        "constant_mix": "恒定比例",
        "dca": "定投渐进",
        "core_satellite": "核心+卫星",
    }.get(s, s)


def cn_sec(s):
    return {
        "banking": "银行",
        "broad-equity": "宽基指数",
        "consumer-staples": "消费",
        "new-energy": "新能源",
    }.get(s, s)


def kc(m, v):
    if v is None:
        return "#64748B"
    if m == "sharpe":
        return "#12B76A" if v > 0.5 else "#F04438" if v < 0 else "#F79009"
    if m == "dd":
        return "#12B76A" if v < 0.10 else "#F04438" if v > 0.20 else "#F79009"
    if m == "vol":
        return "#12B76A" if v < 0.15 else "#F04438" if v > 0.25 else "#F79009"
    return "#0F172A"


def _corr_bg(v):
    a = 0.15 + 0.85 * abs(v)
    if v >= 0.5:
        return f"rgba(192,57,43,{a:.2f})"
    if v >= 0.2:
        return f"rgba(230,126,34,{a:.2f})"
    if v >= 0:
        return f"rgba(47,120,173,{a:.2f})"
    return f"rgba(20,63,116,{a:.2f})"


def _corr_tc(v):
    return "#fff" if abs(v) > 0.55 else "#1A1A1A"


def _health_score(pm, co, cm, lq, fl):
    """Compute weighted health score 0-100 from all diagnosis dimensions."""
    # 1. Return quality (25%) — based on Sharpe
    sr = pm.get("sharpe_ratio") or 0
    s_ret = min(max((sr + 0.5) / 2.0, 0), 1) * 100  # -0.5→0, 1.5→100

    # 2. Volatility control (15%) — ann_volatility
    vol = pm.get("ann_volatility") or 0.20
    s_vol = min(max((0.35 - vol) / 0.30, 0), 1) * 100  # 5%→100, 35%→0

    # 3. Drawdown control (15%) — max_drawdown
    dd = pm.get("max_drawdown") or 0.15
    s_dd = min(max((0.40 - dd) / 0.35, 0), 1) * 100  # 5%→100, 40%→0

    # 4. Diversification (15%) — HHI + high-corr pairs
    hhi = co.get("hhi", 0.5)
    s_div = min(max((0.5 - hhi) / 0.45, 0), 1) * 100  # 0.05→100, 0.5→0
    n_hc = len(cm.get("high_correlation_pairs", []))
    s_div = s_div * max(1 - n_hc * 0.15, 0.2)  # penalise high-corr pairs

    # 5. Liquidity (10%)
    liq_holdings = lq.get("holdings", [])
    if liq_holdings and lq.get("portfolio_market_value") is not None:
        days_list = [h.get("liquidation_days") or 0 for h in liq_holdings]
        max_days = max(days_list) if days_list else 0
        s_liq = min(max((10 - max_days) / 9, 0), 1) * 100  # 1d→100, 10d→0
    else:
        s_liq = 70  # no data, neutral

    # 6. Risk flags (20%)
    n_high = sum(1 for f in fl if f.get("severity") == "high")
    n_mid = sum(1 for f in fl if f.get("severity") == "medium")
    s_flag = max(100 - n_high * 30 - n_mid * 12, 0)

    score = (
        s_ret * 0.25
        + s_vol * 0.15
        + s_dd * 0.15
        + s_div * 0.15
        + s_liq * 0.10
        + s_flag * 0.20
    )
    details = [
        ("收益质量", round(s_ret), 25),
        ("波动控制", round(s_vol), 15),
        ("回撤控制", round(s_dd), 15),
        ("分散化", round(s_div), 15),
        ("流动性", round(s_liq), 10),
        ("风险合规", round(s_flag), 20),
    ]
    return round(score), details


def _score_ring_svg(score, sz=120):
    """SVG donut ring for health score."""
    cx, cy, r = sz // 2, sz // 2, sz // 2 - 10
    circ = 2 * math.pi * r
    pct_val = score / 100
    offset = circ * (1 - pct_val)
    if score >= 80:
        color = "#12B76A"
    elif score >= 60:
        color = "#F79009"
    else:
        color = "#F04438"
    return f'''<svg viewBox="0 0 {sz} {sz}" xmlns="http://www.w3.org/2000/svg" style="width:{sz}px;height:{sz}px">
<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#E2E8F0" stroke-width="8"/>
<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" stroke-width="8"
  stroke-dasharray="{circ:.1f}" stroke-dashoffset="{offset:.1f}"
  stroke-linecap="round" transform="rotate(-90 {cx} {cy})"/>
<text x="{cx}" y="{cy - 4}" text-anchor="middle" font-size="28" font-weight="700" fill="{color}" font-family="Microsoft YaHei,sans-serif">{score}</text>
<text x="{cx}" y="{cy + 14}" text-anchor="middle" font-size="10" fill="#64748B" font-family="Microsoft YaHei,sans-serif">/ 100</text>
</svg>'''


def _radar_svg(vals, labels, sz=260):
    cx, cy, r = sz // 2, sz // 2, sz // 2 - 36
    n = len(vals)
    angs = [math.pi / 2 + 2 * math.pi * i / n for i in range(n)]

    def p(a, rad):
        return cx + rad * math.cos(a), cy - rad * math.sin(a)

    s = [
        f'<svg viewBox="0 0 {sz} {sz}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:{sz}px">'
    ]
    for f in [0.25, 0.5, 0.75, 1]:
        pts = " ".join(f"{p(a, r * f)[0]:.1f},{p(a, r * f)[1]:.1f}" for a in angs)
        s.append(
            f'<polygon points="{pts}" fill="none" stroke="#E2E8F0" stroke-width=".5"/>'
        )
    for a in angs:
        x2, y2 = p(a, r)
        s.append(
            f'<line x1="{cx}" y1="{cy}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#E2E8F0" stroke-width=".5"/>'
        )
    pts = " ".join(
        f"{p(angs[i], r * (vals[i] + 1) / 2)[0]:.1f},{p(angs[i], r * (vals[i] + 1) / 2)[1]:.1f}"
        for i in range(n)
    )
    s.append(
        f'<polygon points="{pts}" fill="rgba(47,120,173,.15)" stroke="#2F78AD" stroke-width="2"/>'
    )
    for i in range(n):
        mp = r * (vals[i] + 1) / 2
        x, y = p(angs[i], mp)
        s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#2F78AD"/>')
        lx, ly = p(angs[i], r + 18)
        s.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" font-size="10" fill="#334155" font-family="Microsoft YaHei,sans-serif">{labels[i]}</text>'
        )
        vx, vy = p(angs[i], mp + 14)
        s.append(
            f'<text x="{vx:.1f}" y="{vy:.1f}" text-anchor="middle" font-size="8" fill="#2F78AD" font-weight="600">{vals[i]:+.2f}</text>'
        )
    s.append("</svg>")
    return "\n".join(s)


def generate_html(result, output_path):
    d = result["data"]
    m = d["metadata"]
    pm = d["risk_metrics"]["portfolio"]
    hs = d["risk_metrics"]["holdings"]
    cm = d["correlation_matrix"]
    fe = d["factor_exposure"]
    rc = d["risk_contribution"]
    co = d["concentration"]
    bm = d["benchmark"]
    se = d["sector_exposure"]
    lq = d["liquidity"]
    fl = d["risk_flags"]
    n = len(cm["labels"])
    sl = [l.split(".")[0] for l in cm["labels"]]
    nm = {h["code"]: h["name"] for h in hs}
    fv = [fe["portfolio"][f] for f in fe["factor_order"]]
    fc = ["市值", "价值", "动量", "质量", "波动", "流动性"]
    radar = _radar_svg(fv, fc)

    # Build sections
    # Holdings table
    hrows = ""
    for h in hs:
        pc = "pos" if h["ann_return_arithmetic"] > 0 else "neg"
        sc = "pos" if (h["sharpe_ratio"] or 0) > 0 else "neg"
        rec = (
            str(h["max_dd_recovery_days"]) + "天恢复"
            if h["max_dd_recovery_days"] is not None
            else "尚未恢复"
        )
        hrows += f'<tr><td class="tl"><b>{h["name"]}</b><br><span class="cd">{h["code"]}</span></td><td>{h["weight_pct"]:.0f}%</td><td class="{pc}">{spct(h["ann_return_arithmetic"])}</td><td>{pct(h["ann_volatility"])}</td><td class="neg">{pct(h["max_drawdown"])}<br><span class="cd">{rec}</span></td><td class="{sc}">{num(h["sharpe_ratio"])}</td><td>{num(h["sortino_ratio"])}</td><td>{pct(h["var_95"])}</td></tr>\n'

    # Correlation cells
    ccells = ""
    for i in range(n):
        ccells += f'<div class="cl">{sl[i]}</div>'
        for j in range(n):
            v = cm["matrix"][i][j]
            ccells += f'<div class="cc" style="background:{_corr_bg(v)};color:{_corr_tc(v)}">{v:.2f}</div>\n'

    # Risk contribution bars
    mx_rc = max((h["pct_risk_contribution"] for h in rc["by_holding"]), default=1)
    rcbars = ""
    for h in sorted(rc["by_holding"], key=lambda x: -x["pct_risk_contribution"]):
        w = h["pct_risk_contribution"] / mx_rc * 100
        name = nm.get(h["code"], h["code"])
        rcbars += f'<div class="rb"><div class="rl">{name}<span class="cd">{h["code"]}</span></div><div class="rw"><div class="rf" style="width:{w:.0f}%"></div><span class="rv">{h["pct_risk_contribution"] * 100:.1f}%</span></div><div class="rr">权重{h["weight_raw"] * 100:.1f}%</div></div>\n'

    # Sector bars
    sebars = ""
    mxse = max(se.values()) if se else 1
    for k, v in sorted(se.items(), key=lambda x: -x[1]):
        w = v / mxse * 100
        sebars += f'<div class="sb"><span class="sl">{cn_sec(k)}</span><div class="sw"><div class="sf" style="width:{w:.0f}%"></div></div><span class="sv">{v * 100:.0f}%</span></div>\n'

    # Flags
    fhtml = ""
    for f in fl:
        cls = "fh" if f["severity"] == "high" else "fm"
        sev_cn = f.get("severity_cn", "高" if f["severity"] == "high" else "中")
        metric_cn = f.get("metric_cn", f["metric"])
        fhtml += f'<div class="fc {cls}"><div class="fhd"><span class="fs">{sev_cn}</span>{metric_cn}</div><div class="fb">{f["explanation"]}</div><div class="fv">实际值 {f["actual_value"]}　·　阈值 {f["threshold"]}</div></div>\n'

    # Liquidity cards (same style as risk flags)
    has_pmv = lq.get("portfolio_market_value") is not None
    liq_cards = ""
    for h in lq["holdings"]:
        name = nm.get(h["code"], h["code"])
        days = h.get("liquidation_days")
        turnover = h["avg_daily_turnover"] / 1e8
        if not has_pmv:
            cls = "fm"
            level = "仅供参考"
            desc = f"{name} 日均成交额 {turnover:.1f} 亿元。未提供总投资金额，无法计算清仓天数。"
            val_line = f"日均成交额 {turnover:.1f} 亿"
        elif days is None or turnover == 0:
            cls = "fm"
            level = "无数据"
            desc = f"{name} 无成交数据，无法评估流动性。"
            val_line = ""
        elif days > 5:
            cls = "fh"
            level = "流动性紧张"
            desc = f"{name} 按当前仓位金额，以不超过日均成交额 10% 的节奏卖出，预计需要 {days:.1f} 个交易日才能全部清仓。大额集中卖出可能压低价格，建议分批减仓。"
            val_line = f"预计清仓 {days:.1f} 天　·　日均成交额 {turnover:.1f} 亿"
        elif days > 1:
            cls = "fm"
            level = "留意"
            desc = f"{name} 预计需要 {days:.1f} 个交易日清仓。日常调仓问题不大，但如果需要一天内全部卖出，可能对市场价格造成一定冲击。"
            val_line = f"预计清仓 {days:.1f} 天　·　日均成交额 {turnover:.1f} 亿"
        else:
            cls = "fg"
            level = "流动性良好"
            desc = f"{name} 当前持仓金额相对其日均成交量非常小，{days:.1f} 天内即可轻松卖出，不会对市场价格造成明显影响。"
            val_line = f"预计清仓 {days:.1f} 天　·　日均成交额 {turnover:.1f} 亿"
        liq_cards += f'<div class="fc {cls}"><div class="fhd"><span class="fs">{level}</span>{name}<span class="cd" style="display:inline;margin-left:6px">{h["code"]}</span></div><div class="fb">{desc}</div><div class="fv">{val_line}</div></div>\n'

    # Warnings — split into data-gap notices vs general warnings
    data_gap_warnings = [
        w
        for w in m.get("warnings", [])
        if "无价格数据" in w or "权重" in w and "分配" in w
    ]
    other_warnings = [w for w in m.get("warnings", []) if w not in data_gap_warnings]
    whtml = "".join(f'<div class="wi">• {w}</div>' for w in other_warnings)

    # Data coverage notice block
    dcov_html = ""
    if data_gap_warnings:
        dcov_items = "".join(
            f'<div class="fc fm"><div class="fb" style="font-size:10px">{w}</div></div>\n'
            for w in data_gap_warnings
        )
        dcov_html = f"""<div class="sp"></div>
<div class="st">10 <span>数据覆盖说明 Data Coverage</span></div>
<div class="nt">以下标的在数据源中未能获取到历史行情数据，其权重已按比例重新分配至其余持仓。<b>这些标的未纳入本次诊断的收益、风险和相关性计算。</b>如需完整评估，请确认标的代码是否正确或联系数据服务商核实数据可用性。</div>
{dcov_items}"""

    # Correlation high pairs
    hphtml = ""
    for p in cm["high_correlation_pairs"]:
        hphtml += f'<div style="font-size:9px;color:#F04438">⚠ {p["pair"][0]} — {p["pair"][1]}: {p["correlation"]:.2f}</div>'
    if not hphtml:
        hphtml = '<div style="font-size:9px;color:#12B76A;margin-top:4px">✓ 无超阈值高相关配对</div>'

    _dc = '<div class="dc">本报告由蓓曦智能 x qVeris 量化引擎自动生成，基于历史数据分析，不构成投资建议。过往表现不代表未来收益。投资有风险，决策需谨慎。</div>'

    health_score, health_details = _health_score(pm, co, cm, lq, fl)
    score_ring = _score_ring_svg(health_score)
    if health_score >= 80:
        hs_label, hs_color = "优秀", "#12B76A"
    elif health_score >= 60:
        hs_label, hs_color = "良好", "#2F78AD"
    elif health_score >= 40:
        hs_label, hs_color = "需关注", "#F79009"
    else:
        hs_label, hs_color = "建议调整", "#F04438"
    hs_bars = ""
    for lbl, val, wt in health_details:
        bc = "#12B76A" if val >= 70 else "#F79009" if val >= 40 else "#F04438"
        hs_bars += f'<div class="hs-bar"><div class="hs-bl">{lbl}</div><div class="hs-bw"><div class="hs-bf" style="width:{val}%;background:{bc}"></div></div><div class="hs-bv">{val}</div></div>\n'

    pc = lambda m, v: kc(m, v)

    html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>投资组合深度量化诊断报告</title>
<style>
@page{{size:A4;margin:0}}
@media print{{.pg{{page-break-after:always;page-break-inside:avoid}}.pg:last-child{{page-break-after:auto}}body{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:"Microsoft YaHei","PingFang SC","Noto Sans SC",system-ui,sans-serif;color:#0F172A;background:#fff;font-size:11px;line-height:1.6}}
.pg{{width:210mm;min-height:297mm;position:relative;overflow:hidden}}
/* Cover */
.cv{{background:#0F2440;color:#fff;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center}}
.cv-br{{font-size:10px;letter-spacing:6px;text-transform:uppercase;opacity:.6;margin-bottom:10px}}
.cv-lg{{display:flex;gap:20px;align-items:baseline;margin-bottom:44px}}
.cv-lg .bx{{font-size:28px;font-weight:700;letter-spacing:2px}}
.cv-lg .dv{{width:1px;height:26px;background:rgba(255,255,255,.3)}}
.cv-lg .qv{{font-size:26px;font-weight:700;letter-spacing:3px;opacity:.9}}
.cv h1{{font-size:30px;font-weight:200;letter-spacing:3px;margin-bottom:5px}}
.cv h2{{font-size:12px;font-weight:300;letter-spacing:8px;text-transform:uppercase;opacity:.55;margin-bottom:48px}}
.cv-mt{{display:grid;grid-template-columns:1fr 1fr;gap:1px 28px;text-align:left;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:18px 24px;max-width:420px}}
.cv-mt dt{{font-size:8px;text-transform:uppercase;letter-spacing:2px;opacity:.45}}
.cv-mt dd{{font-size:12px;font-weight:500;margin-bottom:8px}}
.cv-ft{{position:absolute;bottom:24px;font-size:7.5px;opacity:.3;letter-spacing:1px}}
/* Content */
.ct{{padding:24px 28px 16px}}
.pn{{position:absolute;bottom:14px;right:28px;font-size:7.5px;color:#94A3B8}}
.st{{font-size:8.5px;letter-spacing:3px;text-transform:uppercase;color:#2F78AD;border-bottom:2px solid #2F78AD;padding-bottom:3px;margin-bottom:12px;font-weight:600}}
.st span{{color:#143F74;font-weight:300;margin-left:6px;letter-spacing:1px}}
.gl{{height:3px;background:linear-gradient(90deg,#143F74,#2F78AD,#6EB0C2);border-radius:2px;margin-bottom:12px}}
/* KPI */
.kg{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px}}
.kp{{background:#F8FAFC;border:1px solid #E2E8F0;border-radius:5px;padding:10px 12px}}
.kl{{font-size:8.5px;color:#64748B;letter-spacing:1px;text-transform:uppercase;margin-bottom:1px}}
.kv{{font-size:22px;font-weight:700;letter-spacing:-.5px}}
.ks{{font-size:8.5px;color:#94A3B8;margin-top:1px}}
/* Table */
table{{width:100%;border-collapse:collapse;font-size:10px}}
th{{background:#143F74;color:#fff;padding:5px 7px;text-align:center;font-weight:500;font-size:9px;letter-spacing:.5px}}
td{{padding:4px 7px;text-align:center;border-bottom:1px solid #E2E8F0}}
tr:nth-child(even){{background:#F8FAFC}}
tr:last-child{{font-weight:600;background:#EEF2FF}}
.tl{{text-align:left;font-weight:500}}
.cd{{font-size:8px;color:#94A3B8;font-weight:400}}
.pos{{color:#12B76A}}.neg{{color:#F04438}}
/* Corr */
.cg{{display:grid;grid-template-columns:64px repeat({n},1fr);gap:2px;margin-bottom:10px}}
.cl{{font-size:7.5px;display:flex;align-items:center;justify-content:center;color:#334155;font-weight:600}}
.cc{{display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:600;border-radius:3px;min-height:40px}}
/* 2col */
.tc{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
/* RC bars */
.rb{{display:grid;grid-template-columns:90px 1fr 60px;align-items:center;margin-bottom:5px}}
.rl{{font-size:9.5px;font-weight:500}}
.rw{{position:relative;height:16px;background:#F1F5F9;border-radius:3px;overflow:hidden}}
.rf{{height:100%;background:linear-gradient(90deg,#143F74,#2F78AD);border-radius:3px}}
.rv{{position:absolute;right:5px;top:0;font-size:8px;font-weight:600;color:#143F74;line-height:16px}}
.rr{{font-size:7.5px;color:#94A3B8;text-align:right}}
/* Sector */
.sb{{display:grid;grid-template-columns:60px 1fr 32px;align-items:center;margin-bottom:3px}}
.sl{{font-size:9.5px;color:#334155}}.sw{{height:11px;background:#F1F5F9;border-radius:2px;overflow:hidden}}
.sf{{height:100%;background:#6EB0C2;border-radius:2px}}.sv{{font-size:8px;font-weight:600;color:#2F78AD;text-align:right}}
/* Flags */
.fc{{border-left:3px solid;padding:7px 10px;margin-bottom:7px;background:#F8FAFC;border-radius:0 4px 4px 0}}
.fh{{border-color:#F04438}}.fm{{border-color:#F79009}}.fg{{border-color:#12B76A}}
.fhd{{font-size:10.5px;font-weight:600;margin-bottom:2px}}
.fs{{font-size:7.5px;padding:1px 5px;border-radius:3px;color:#fff;margin-right:5px}}
.fh .fs{{background:#F04438}}.fm .fs{{background:#F79009}}.fg .fs{{background:#12B76A}}
.fb{{font-size:10px;color:#334155;line-height:1.5}}.fv{{font-size:8.5px;color:#94A3B8;margin-top:2px}}
.wi{{font-size:9.5px;color:#64748B;margin-bottom:3px;padding-left:8px;line-height:1.5}}
/* Mini table */
.mt{{table-layout:fixed;width:100%}}.mt td{{font-size:10px;padding:4px 8px}}.mt td:first-child{{color:#64748B;width:22%}}.mt td:nth-child(2){{width:18%}}.mt td:last-child{{font-weight:600}}
/* Annotations — readable at A4 print */
.nt{{font-size:10.5px;color:#475569;margin-bottom:10px;line-height:1.65;padding:8px 12px;background:linear-gradient(135deg,#F8FAFC,#F1F5F9);border-left:3px solid #6EB0C2;border-radius:0 6px 6px 0}}
.nt b{{color:#0F172A}}
.kn{{font-size:8.5px;color:#64748B;margin-top:3px;line-height:1.45}}
.fn{{font-size:8.5px;color:#64748B;font-weight:400;padding-left:6px;line-height:1.4}}
.g{{color:#12B76A;font-weight:600}}.r{{color:#F04438;font-weight:600}}
/* Health score */
.hs-wrap{{display:flex;align-items:center;gap:18px;margin-bottom:14px;padding:14px 18px;background:linear-gradient(135deg,#F8FAFC,#EEF2FF);border:1px solid #E2E8F0;border-radius:8px}}
.hs-ring{{flex-shrink:0}}
.hs-info{{flex:1}}
.hs-title{{font-size:14px;font-weight:700;color:#0F172A;margin-bottom:2px}}
.hs-sub{{font-size:10px;color:#64748B;margin-bottom:8px}}
.hs-bars{{display:grid;grid-template-columns:1fr 1fr;gap:4px 12px}}
.hs-bar{{display:flex;align-items:center;gap:6px}}
.hs-bl{{font-size:8.5px;color:#64748B;width:48px;text-align:right}}
.hs-bw{{flex:1;height:8px;background:#E2E8F0;border-radius:4px;overflow:hidden}}
.hs-bf{{height:100%;border-radius:4px}}.hs-bv{{font-size:8px;font-weight:600;color:#334155;width:22px}}
.sp{{height:10px}}.dc{{position:absolute;bottom:16px;left:28px;right:28px;font-size:7px;color:#94A3B8;text-align:center;border-top:1px solid #E2E8F0;padding-top:6px}}
</style></head><body>

<!-- COVER -->
<div class="pg cv">
<div class="cv-br">Portfolio Quantitative Diagnosis</div>
<div class="cv-lg"><div class="bx">蓓曦智能</div><div class="dv"></div><div class="qv">qVeris</div></div>
<h1>投资组合深度量化诊断</h1>
<h2>Deep Quantitative Diagnosis Report</h2>
<dl class="cv-mt">
<dt>风险偏好</dt><dd>{cn_tol(m["risk_tolerance"])}</dd>
<dt>投资期限</dt><dd>{m["investment_horizon"]}</dd>
<dt>仓位风格</dt><dd>{cn_sty(m["position_style"])}</dd>
<dt>数据频率</dt><dd>{m["data_frequency"]} · {m["lookback_period"]}</dd>
<dt>数据范围</dt><dd>{m["date_range"][0]} ~ {m["date_range"][1]}</dd>
<dt>数据点数</dt><dd>{m["data_points"]} bars · AF {m["annualization_factor"]}</dd>
</dl>
<div class="cv-ft">Generated {m["computed_at"][:10]} · BEIXI AI × qVeris Quantitative Engine</div>
</div>

<!-- PAGE 2: KPI + TABLE -->
<div class="pg"><div class="ct">
<div class="gl"></div>
<div class="hs-wrap">
<div class="hs-ring">{score_ring}</div>
<div class="hs-info">
<div class="hs-title">组合健康度 <span style="color:{hs_color};margin-left:6px">{health_score} 分 · {hs_label}</span></div>
<div class="hs-sub">综合收益质量、波动控制、回撤深度、分散化程度、流动性和风险合规六个维度加权评估</div>
<div class="hs-bars">
{hs_bars}</div>
</div>
</div>
<div style="height:40px"></div>
<div class="st">01 <span>风险指标总览 Risk Metrics Overview</span></div>
<div class="nt">这一页是您投资组合的「体检报告单」。我们从三个角度检查了您的组合：<b>赚了多少</b>（收益）、<b>波动有多大</b>（风险）、<b>最坏情况会怎样</b>（尾部风险）。数字旁的颜色可以帮您快速判断：<span class="g">绿色 = 健康</span>，<span style="color:#F79009">橙色 = 需留意</span>，<span class="r">红色 = 建议关注</span>。</div>
<div class="kg">
<div class="kp"><div class="kl">年化收益 CAGR</div><div class="kv" style="color:{pc("sharpe", pm["cagr"])}">{spct(pm["cagr"])}</div><div class="ks">算术 {spct(pm["ann_return_arithmetic"])}</div><div class="kn">如果把过去这段时间的总收益「摊平」到每一年，您的组合每年大约增长这么多。正数代表赚钱，负数代表亏损。</div></div>
<div class="kp"><div class="kl">年化波动率</div><div class="kv" style="color:{pc("vol", pm["ann_volatility"])}">{pct(pm["ann_volatility"])}</div><div class="ks">下行 {pct(pm["downside_volatility"])}</div><div class="kn">您的组合价格每天上下跳动的幅度。数字越大，说明账户金额每天变化越剧烈。15%以下算比较稳，25%以上波动就比较大了。「下行」只算下跌的部分。</div></div>
<div class="kp"><div class="kl">最大回撤</div><div class="kv" style="color:{pc("dd", pm["max_drawdown"])}">{pct(pm["max_drawdown"])}</div><div class="ks">{str(pm["max_dd_recovery_days"]) + "个交易日后回本" if pm["max_dd_recovery_days"] else "截至报告期末尚未回本"}</div><div class="kn">在分析期间内，您的组合从最高点到最低点最多亏损过多少。比如 7% 意味着如果您在最高点投入 100 万，最多曾缩水到 93 万。上方小字显示从最低点到重新回到之前高点花了多久。</div></div>
<div class="kp"><div class="kl">夏普比率</div><div class="kv" style="color:{pc("sharpe", pm["sharpe_ratio"])}">{num(pm["sharpe_ratio"])}</div><div class="ks">rf = 1.5%</div><div class="kn">衡量「性价比」的指标——您每承受一份波动，换来了多少超过银行存款的收益。大于 1 说明回报丰厚且风险可控；低于 0 说明还不如存银行。</div></div>
<div class="kp"><div class="kl">索提诺 / 卡玛</div><div class="kv">{num(pm["sortino_ratio"])}</div><div class="ks">Calmar {num(pm["calmar_ratio"])}</div><div class="kn">索提诺和夏普类似，但只关心「下跌」的波动，不惩罚向上的波动——毕竟涨得多不算坏事。卡玛则看收益和最大回撤的比例：赚 10% 但回撤过 20%，说明赚的不够补回撤的心理压力。</div></div>
<div class="kp"><div class="kl">VaR / CVaR 95%</div><div class="kv" style="color:#F04438">{pct(pm["var_95"])}</div><div class="ks">CVaR {pct(pm["cvar_95"])} · 最差 {pct(pm["worst_period"])}</div><div class="kn">回答一个关键问题：「最倒霉的一天会亏多少？」在正常市场环境下（95%的交易日），单日最大亏损不超过 VaR 这个数字。而 CVaR 衡量的是一旦突破这个底线，平均还会再亏多少——这反映极端行情下的风险。</div></div>
</div>
<div class="nt" style="margin-bottom:4px">下方表格列出了您持有的每一只标的，以及它们各自的表现。<b>最后一行蓝底</b>是整个组合的综合表现——多只持仓组合在一起后，通常波动和回撤会低于单只最差的那个（这就是分散投资的好处）。</div>
<table><thead><tr><th style="text-align:left">标的</th><th>权重</th><th>年化收益</th><th>波动率</th><th>最大回撤</th><th>夏普</th><th>索提诺</th><th>VaR95</th></tr></thead>
<tbody>{hrows}
<tr><td class="tl"><b>组合 Portfolio</b></td><td>100%</td><td class="{"pos" if pm["ann_return_arithmetic"] > 0 else "neg"}">{spct(pm["ann_return_arithmetic"])}</td><td>{pct(pm["ann_volatility"])}</td><td class="neg">{pct(pm["max_drawdown"])}<br><span class="cd">{str(pm["max_dd_recovery_days"]) + "天恢复" if pm["max_dd_recovery_days"] else "尚未恢复"}</span></td><td class="{"pos" if (pm["sharpe_ratio"] or 0) > 0 else "neg"}">{num(pm["sharpe_ratio"])}</td><td>{num(pm["sortino_ratio"])}</td><td>{pct(pm["var_95"])}</td></tr>
</tbody></table>
{_dc}
</div><div class="pn">02</div></div>

<!-- PAGE 3: CORR + FACTOR -->
<div class="pg"><div class="ct">
<div class="gl"></div>
<div class="tc">
<div>
<div class="st">02 <span>相关性矩阵 Correlation</span></div>
<div class="nt">这张表格回答一个问题：<b>您的持仓会不会一起跌？</b>每个格子里的数字从 -1 到 +1，含义如下：<br><b>接近 +1（红色）</b>= 两只股票涨跌几乎同步，如果一只跌了另一只大概率也跌，把鸡蛋放在了同一个篮子里。<br><b>接近 0（浅色）</b>= 两只股票各走各的，一只跌的时候另一只不一定跌——这就是好的分散效果。<br><b>负值（蓝色）</b>= 一只涨的时候另一只倾向于跌，天然的对冲关系，非常理想。</div>
<div class="cg">
<div></div>{"".join(f'<div class="cl">{l}</div>' for l in sl)}
{ccells}
</div>
{hphtml}
</div>
<div>
<div class="st">03 <span>因子暴露 Factor Exposure</span></div>
<div class="nt">您的组合像一个人有不同的「性格特征」。雷达图上每个方向代表一种投资风格，离中心越远说明这个特征越明显。<b>形状越均匀</b>说明组合风格越均衡；如果某个方向特别突出，意味着组合对那种市场环境特别敏感。</div>
{radar}
<table class="mt" style="margin-top:6px">
<tr><td>市值 Size</td><td style="color:#2F78AD">{fv[0]:+.2f}</td><td class="fn">偏正 = 持有的多是大公司；偏负 = 偏小盘股</td></tr>
<tr><td>价值 Value</td><td style="color:#2F78AD">{fv[1]:+.2f}</td><td class="fn">偏正 = 偏便宜的、高分红的股票；偏负 = 偏高成长的</td></tr>
<tr><td>动量 Momentum</td><td style="color:#2F78AD">{fv[2]:+.2f}</td><td class="fn">偏正 = 持有的近期涨得好；偏负 = 近期表现弱</td></tr>
<tr><td>质量 Quality</td><td style="color:#2F78AD">{fv[3]:+.2f}</td><td class="fn">偏正 = 公司盈利强、负债低；偏负 = 基本面偏弱</td></tr>
<tr><td>波动 Volatility</td><td style="color:#2F78AD">{fv[4]:+.2f}</td><td class="fn">偏正 = 持有的本身波动大；偏负 = 本身波动小</td></tr>
<tr><td>流动性 Liquidity</td><td style="color:#2F78AD">{fv[5]:+.2f}</td><td class="fn">偏正 = 买卖活跃容易交易；偏负 = 成交清淡</td></tr>
</table>
</div>
</div>
<div class="sp"></div>
<div class="st">04 <span>风险贡献 Risk Contribution</span></div>
<div class="nt">您的每一只持仓对组合整体波动的「贡献」并不相同。比如某只股票虽然只占 25% 的仓位，但它波动很大，可能贡献了 50% 以上的风险。下方左侧深蓝色柱是每只标的的风险贡献，右侧灰色是它的权重。<b>如果深蓝柱远长于灰色柱</b>，说明这只标的「拖累」组合风险的程度远超其仓位占比，是组合波动的主要来源。</div>
{rcbars}
{_dc}
</div><div class="pn">03</div></div>

<!-- PAGE 4: SECTOR + CONC + BENCH + LIQ -->
<div class="pg"><div class="ct">
<div class="gl"></div>
<div class="tc">
<div>
<div class="st">05 <span>行业暴露 Sector</span></div>
<div class="nt">您的钱分别投在了哪些行业？如果大部分资金集中在一两个行业，一旦该行业遇到利空消息（比如政策调控、行业周期下行），组合会受到较大冲击。理想状态是分布在多个不同行业。</div>
{sebars}
<div class="sp"></div>
<div class="st">06 <span>集中度 Concentration</span></div>
<div class="nt">用几个数字回答：<b>您的组合分散得够不够？</b>想象把全部资金平均分给 N 只股票——Effective N 就告诉您，您当前的集中程度相当于平均持有几只。数字越大越分散，越小说明资金越集中在少数标的上。</div>
<table class="mt">
<tr><td>HHI 赫芬达尔指数</td><td>{co["hhi"]:.4f}</td><td class="fn">接近 0 = 非常分散；接近 1 = 几乎全押一只</td></tr>
<tr><td>Effective N 等效持仓数</td><td>{co["effective_n"]:.1f}</td><td class="fn">虽然您持有多只标的，但由于仓位不均，分散效果相当于持有 {co["effective_n"]:.1f} 只等权股票</td></tr>
<tr><td>前三大占比</td><td>{co["top_3_pct"]:.0%}</td><td class="fn">前三大持仓合计占了您组合的 {co["top_3_pct"]:.0%}，超过 80% 通常认为偏集中</td></tr>
<tr><td>单持仓最大</td><td>{co["max_holding_pct"]:.0%}</td><td class="fn">您最重的一只持仓占比，对应{cn_tol(m["risk_tolerance"])}型投资者的参考上限进行判断</td></tr>
<tr><td>单行业最大</td><td>{co["max_sector_pct"]:.0%}</td><td class="fn">您在同一行业的最大合计占比</td></tr>
</table>
</div>
<div>
<div class="st">07 <span>基准对比 Benchmark</span></div>
<div class="nt">单独看收益高低还不够，还要和「大盘」比。就像考试不仅看绝对分数，还要看在班级里排第几。我们根据您持仓的市值特征，自动选择了一个合适的市场基准来做对比。</div>
<table class="mt">
<tr><td>基准指数</td><td>{bm["benchmark_name"]} ({bm["benchmark_code"]})</td><td class="fn">代表整个市场的平均表现，作为您组合的比较对象</td></tr>
<tr><td>选择依据</td><td>{bm.get("selection_rule", "—")}</td><td class="fn">按持仓加权平均市值自动判断偏大盘还是偏中小盘</td></tr>
<tr><td>Beta 贝塔</td><td>{num(bm.get("beta"))}</td><td class="fn">您的组合跟着大盘走的程度。= 1 表示完全跟随，< 1 表示比大盘稳，> 1 表示比大盘波动更大</td></tr>
<tr><td>Alpha 超额收益</td><td class="{"pos" if (bm.get("alpha_annual") or 0) > 0 else ""}">{spct(bm.get("alpha_annual"))}</td><td class="fn">扣除大盘涨跌影响后，您的组合额外多赚（或少赚）了多少。正数说明选股有效</td></tr>
<tr><td>跟踪误差</td><td>{pct(bm.get("tracking_error"))}</td><td class="fn">您的组合和大盘走势偏离多少。越小越像指数基金，越大说明选股越「有主见」</td></tr>
<tr><td>信息比率</td><td>{num(bm.get("information_ratio"))}</td><td class="fn">每偏离大盘一分，您额外赚了多少。> 0.5 算比较好，说明主动选股的偏离是值得的</td></tr>
<tr><td>相对最大回撤</td><td>{pct(bm.get("relative_max_drawdown"))}</td><td class="fn">在同一时期里，您的组合最多落后大盘多少。越小说明相对大盘越稳</td></tr>
</table>
</div>
</div>
<div class="sp"></div>
<div class="st">08 <span>流动性 Liquidity</span></div>
<div class="nt">流动性回答一个实际问题：<b>如果您急需用钱，这些股票能多快卖掉？</b>我们根据您的总投资金额和每只股票每天的真实成交量，计算出以不影响市场价格的节奏（按日均成交额的 10%）卖出，分别需要几天。<b>天数越少越好</b>——超过 5 天意味着该持仓在紧急情况下难以快速变现。</div>
{liq_cards}
{_dc}
</div><div class="pn">04</div></div>

<!-- PAGE 5: FLAGS + DATA COVERAGE -->
<div class="pg"><div class="ct">
<div class="gl"></div>
<div class="st">09 <span>风险标记 Risk Flags</span></div>
<div class="nt">我们根据您的风险偏好（<b>{cn_tol(m["risk_tolerance"])}型</b>）和投资期限（<b>{m["investment_horizon"]}</b>），为您设定了对应的安全阈值。当某项指标超出阈值时，会触发下方的标记提醒。<span class="r">「高」级别</span>意味着该项已明显超出安全范围，建议优先调整；<span style="color:#F79009">「中」级别</span>尚在可接受边缘，建议留意观察。没有标记不代表完美，只是说明各项指标都在您的风险承受范围内。</div>
{fhtml if fhtml else '<div style="color:#12B76A;font-size:10px;margin:6px 0">✓ 所有指标均在阈值范围内，未触发风险标记</div>'}
{dcov_html}
{f'<div class="sp"></div><div class="st">{"11" if dcov_html else "10"} <span>其他提示 Notes</span></div>' + whtml if whtml else ""}
{_dc}
</div><div class="pn">05</div></div>

</body></html>'''

    Path(output_path).write_text(html, encoding="utf-8")
    return Path(output_path)


if __name__ == "__main__":
    scenario = sys.argv[1] if len(sys.argv) > 1 else "scenario_moderate.json"
    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)
    output = (
        sys.argv[2] if len(sys.argv) > 2 else str(out_dir / "diagnosis_report.html")
    )
    result = run_diagnosis(
        ASSETS / "scenarios" / scenario,
        ASSETS / "sample-portfolios" / "sample_portfolio_a_shares_growth.csv",
        ASSETS / "stock_data" / "stock_data",
        ASSETS / "stock_data" / "fundamental_data.csv",
        ASSETS / "stock_data" / "benchmark_daily.csv",
    )
    if result["status"] != "ok":
        print(f"Error: {result['error_message']}")
        sys.exit(1)
    p = generate_html(result, output)
    print(f"Report: {p.resolve()}")
    print("Chrome -> Ctrl+P -> Save as PDF (margins:none, background graphics:on)")
