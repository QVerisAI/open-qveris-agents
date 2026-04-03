"""WA10: _internal round-trip tests."""
import pytest
import numpy as np
import pandas as pd

from data_loader import Holding
from pipeline_main import _build_internal, _serialize_series, _serialize_holding
from prescription_main import (
    run_optimization,
    _validate_internal,
    InternalValidationError,
    _deserialize_series,
    _deserialize_holdings,
)


class TestSerializationRoundTrip:
    """Items 1-4: serialize in pipeline_main, deserialize in prescription_main."""

    def test_holding_returns_index_values_structure(self):
        """Item 1: serialized holding_returns have {index, values}."""
        dates = pd.date_range("2025-01-01", periods=50, freq="B")
        series = pd.Series(np.random.normal(0.001, 0.02, 50), index=dates)
        serialized = _serialize_series(series)
        assert "index" in serialized
        assert "values" in serialized
        assert len(serialized["index"]) == len(serialized["values"]) == 50

    def test_benchmark_returns_index_values_structure(self):
        dates = pd.date_range("2025-01-01", periods=30, freq="B")
        series = pd.Series(np.random.normal(0.002, 0.01, 30), index=dates)
        serialized = _serialize_series(series)
        assert len(serialized["index"]) == len(serialized["values"]) == 30

    def test_deserialized_series_has_datetime_index(self):
        """Item 3: deserialized returns are pd.Series with DatetimeIndex."""
        serialized = {"index": ["2025-01-02", "2025-01-03"], "values": [0.01, -0.005]}
        s = _deserialize_series(serialized)
        assert isinstance(s, pd.Series)
        assert isinstance(s.index, pd.DatetimeIndex)
        assert len(s) == 2

    def test_deserialized_holdings_are_holding_dataclass(self):
        """Item 3: deserialized holdings are list[Holding]."""
        dicts = [
            {"ticker": "600519.SH", "position_name": "Test", "weight_pct": 30.0,
             "vehicle_type": "stock", "sector_theme": "Food", "style_tag": "CPO"},
        ]
        holdings = _deserialize_holdings(dicts)
        assert len(holdings) == 1
        assert isinstance(holdings[0], Holding)
        assert holdings[0].ticker == "600519.SH"
        assert holdings[0].sector_theme == "Food"
        assert holdings[0].style_tag == "CPO"
        assert holdings[0].is_cash is False

    def test_series_alignment_after_roundtrip(self):
        """Item 4: deserialized Series work with pd.DataFrame.dropna() alignment."""
        dates = pd.date_range("2025-01-01", periods=10, freq="B")
        holding = pd.Series([0.01, None, 0.02, 0.03, None, -0.01, 0.005, 0.008, -0.003, 0.01], index=dates)
        benchmark = pd.Series([0.005, 0.002, None, 0.001, -0.002, 0.003, 0.001, -0.001, 0.002, 0.004], index=dates)

        # Serialize
        h_ser = _serialize_series(holding)
        b_ser = _serialize_series(benchmark)

        # Deserialize
        h_de = _deserialize_series(h_ser)
        b_de = _deserialize_series(b_ser)

        # This is the exact operation stress.py does
        aligned = pd.DataFrame({"h": h_de, "b": b_de}).dropna()
        assert len(aligned) > 0
        assert "h" in aligned.columns
        assert "b" in aligned.columns


class TestBuildInternal:
    """Item 1: _build_internal produces correct structure."""

    def test_all_five_keys_present(self):
        dates = pd.date_range("2025-01-01", periods=5, freq="B")
        captured = {
            "holding_returns": {
                "A": pd.Series([0.01, -0.005, 0.02, 0.01, -0.003], index=dates),
            },
            "benchmark_returns": pd.Series([0.002, -0.001, 0.004, 0.001, -0.002], index=dates),
            "holdings": [Holding(position_name="Test", ticker="A", weight_pct=90.0)],
            "cash_pct": 10.0,
        }
        payload = {"params": {"portfolio_market_value": 500000}}
        internal = _build_internal(captured, payload)

        assert set(internal.keys()) == {"holding_returns", "benchmark_returns", "holdings", "cash_pct", "portfolio_market_value"}
        assert internal["cash_pct"] == 10.0
        assert internal["portfolio_market_value"] == 500000

    def test_null_benchmark_returns(self):
        captured = {
            "holding_returns": {},
            "benchmark_returns": None,
            "holdings": [],
            "cash_pct": 0.0,
        }
        payload = {"params": {}}
        internal = _build_internal(captured, payload)
        assert internal["benchmark_returns"] is None
        assert internal["portfolio_market_value"] is None

    def test_holding_serialization_complete(self):
        h = Holding(
            position_name="Test", ticker="600519.SH", weight_pct=30.0,
            sector_theme="Food", style_tag="CPO", lookthrough_group="group1",
        )
        serialized = _serialize_holding(h)
        assert serialized["ticker"] == "600519.SH"
        assert serialized["sector_theme"] == "Food"
        assert serialized["style_tag"] == "CPO"
        assert serialized["lookthrough_group"] == "group1"
        assert "notes" in serialized  # all 11 fields present


class TestInternalValidatorFromRoundTrip:
    """Validates _build_internal output passes _validate_internal."""

    def test_build_internal_output_passes_validator(self):
        dates = pd.date_range("2025-01-01", periods=5, freq="B")
        captured = {
            "holding_returns": {"A": pd.Series([0.01, -0.005, 0.02, 0.01, -0.003], index=dates)},
            "benchmark_returns": pd.Series([0.002, -0.001, 0.004, 0.001, -0.002], index=dates),
            "holdings": [Holding(position_name="Test", ticker="A", weight_pct=90.0)],
            "cash_pct": 10.0,
        }
        payload = {"params": {"portfolio_market_value": 500000}}
        internal = _build_internal(captured, payload)
        _validate_internal(internal)  # should not raise


class TestExecStatsNotLeaked:
    """Item 5: prescription_main.py output must not contain _exec_stats."""

    def test_no_exec_stats_in_output(self):
        from tests.test_prescription_main import _make_envelope
        result = run_optimization(_make_envelope(flags=[]))
        assert result["status"] == "ok"
        assert "_exec_stats" not in result["data"]
        assert "execution_info" in result["data"]
