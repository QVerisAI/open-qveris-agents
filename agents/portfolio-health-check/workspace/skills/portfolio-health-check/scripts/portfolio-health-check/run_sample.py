"""Run diagnosis on sample data and print results."""
import json
import sys

from asset_paths import get_assets_dir
from diagnosis import run_diagnosis

ASSETS = get_assets_dir(__file__)


def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else "scenario_moderate.json"

    result = run_diagnosis(
        ASSETS / "scenarios" / scenario,
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
