"""Run diagnosis on sample data and print results."""
import json
import re
import sys
from pathlib import Path

from asset_paths import get_assets_dir
from diagnosis import run_diagnosis
from _security import UnsafePathError, safe_resolve

ASSETS = get_assets_dir(__file__)

_SAFE_SCENARIO_NAME = re.compile(r"^[A-Za-z0-9._-]+\.json$")


def main():
    raw = sys.argv[1] if len(sys.argv) > 1 else "scenario_moderate.json"
    scenario = Path(raw).name  # 先剥离目录，再走白名单 + 越界校验
    if not _SAFE_SCENARIO_NAME.match(scenario):
        sys.stderr.write(f"非法场景名: {raw!r}\n")
        raise SystemExit(2)
    try:
        scenario_path = safe_resolve(ASSETS / "scenarios" / scenario, allowed_roots=[ASSETS])
    except UnsafePathError as exc:
        sys.stderr.write(f"场景路径不安全: {exc}\n")
        raise SystemExit(2)

    result = run_diagnosis(
        scenario_path,
        ASSETS / "sample-portfolios" / "sample_portfolio_a_shares_growth.csv",
        ASSETS / "stock_data" / "stock_data",
        ASSETS / "stock_data" / "fundamental_data.csv",
        ASSETS / "stock_data" / "benchmark_daily.csv",
    )

    # Use UTF-8 for output
    output = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    sys.stdout.buffer.write(output.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")


if __name__ == "__main__":
    main()
