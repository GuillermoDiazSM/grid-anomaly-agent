import numpy as np
import pandas as pd

from grid_anomaly_agent import anomaly


def _daily(days=200, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=days, freq="D")
    weekly = np.where(idx.dayofweek >= 5, 0.8, 1.0)          # weekends 20 % lower
    return pd.Series(700_000 * weekly * rng.normal(1, 0.01, days), index=idx)


def test_weekly_pattern_alone_is_not_anomalous():
    scored = anomaly.score(_daily(), anomaly.PRESETS["demand"])
    assert (scored["severity"].isin(["high", "medium"])).sum() <= 2


def test_injected_drop_is_high_severity():
    s = _daily()
    s.iloc[150] *= 0.7
    scored = anomaly.score(s, anomaly.PRESETS["demand"])
    assert scored["severity"].iloc[150] == "high"
    assert scored["z"].iloc[150] < -5


def test_outlier_does_not_contaminate_its_own_estimate():
    s = _daily()
    s.iloc[150] *= 3
    scored = anomaly.score(s, anomaly.PRESETS["demand"])
    # the point is scored against history that excludes it
    assert scored["n_history"].iloc[150] == anomaly.PRESETS["demand"].window


def test_short_history_falls_back_to_relative_change():
    s = _daily(days=14)               # only 6 past weekly changes before the last point (< 8)
    s.iloc[13] *= 0.25
    scored = anomaly.score(s, anomaly.PRESETS["demand"])
    assert scored["method"].iloc[13] == "relative_change"
    assert scored["severity"].iloc[13] == "medium"      # > 70 % change, degraded one level


def test_points_without_reference_are_not_evaluable():
    scored = anomaly.score(_daily(), anomaly.PRESETS["demand"])
    assert (scored["severity"].iloc[:7] == "not_evaluable").all()


def test_minimum_magnitude():
    s = _daily()
    s.iloc[100] = 10.0
    p = anomaly.Params(season=7, min_magnitude=1000)
    assert anomaly.score(s, p)["severity"].iloc[100] == "not_evaluable"


def test_prices_with_zeros_use_differences():
    idx = pd.date_range("2025-01-01", periods=24 * 120, freq="h")
    rng = np.random.default_rng(1)
    base = np.where((idx.hour >= 11) & (idx.hour <= 16), 0.0, 80.0)  # solar hours at zero
    s = pd.Series(base + rng.normal(0, 3, len(idx)), index=idx)
    s.iloc[-10] += 150
    scored = anomaly.score(s, anomaly.PRESETS["spot_price"])
    assert scored["severity"].iloc[-10] == "high"
