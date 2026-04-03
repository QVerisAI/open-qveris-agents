"""Correlation matrix and high-correlation pair flagging."""
from __future__ import annotations

import pandas as pd


def compute_correlation_matrix(
    holding_returns: dict[str, pd.Series],
) -> pd.DataFrame:
    """Pearson correlation matrix for non-cash holdings."""
    df = pd.DataFrame(holding_returns).dropna()
    return df.corr(method="pearson")


def flag_high_correlations(
    corr_matrix: pd.DataFrame,
    threshold: float,
) -> list[dict]:
    """Return list of {pair: [a, b], correlation: float} where |corr| > threshold."""
    flags = []
    labels = list(corr_matrix.columns)
    n = len(labels)
    for i in range(n):
        for j in range(i + 1, n):
            val = corr_matrix.iloc[i, j]
            if abs(val) > threshold:
                flags.append({
                    "pair": [labels[i], labels[j]],
                    "correlation": round(val, 2),
                })
    return flags
