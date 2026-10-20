"""Anomaly detection against a series' own history, with robust statistics.

Method
------
For each point the seasonal change is computed against the same point one
season earlier (same weekday last week for daily data, same hour last week
for hourly data):

    log-ratio   r_t = ln(x_t / x_{t-s})      (strictly positive series)
    difference  r_t = x_t - x_{t-s}          (series that can be zero or negative, e.g. prices)

Comparing with the previous season removes the weekly pattern without
modelling it, and the log makes halving and doubling weigh the same.

The change is scored with the modified z-score (Iglewicz & Hoaglin, 1993)
against the trailing history of changes, excluding the point itself so an
outlier cannot contaminate its own estimator:

    z_t = 0.6745 * (r_t - median(r)) / MAD(r)

Severity
--------
    high            |z| > 5.0
    medium          |z| > 3.5   (the threshold recommended by Iglewicz & Hoaglin)
    low             fallback when history is too short or MAD = 0: |x_t / x_{t-s} - 1| above a
                    relative threshold, one level lower because the criterion is less reliable
    not_evaluable   magnitude below the minimum, or no usable history: reported explicitly so
                    "no anomaly" and "could not be checked" are never confused

All thresholds are starting points to be calibrated on the data.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

K = 0.6745  # makes MAD a consistent estimator of the standard deviation under normality


@dataclass(frozen=True)
class Params:
    season: int                     # periods per season (7 for daily, 168 for hourly)
    transform: str = "log_ratio"    # "log_ratio" | "difference"
    window: int = 52                # trailing number of past changes used for median/MAD
    min_history: int = 8            # minimum valid past changes to use the z-score
    z_medium: float = 3.5
    z_high: float = 5.0
    fallback_low: float = 0.35      # relative change thresholds when history is short
    fallback_medium: float = 0.70
    min_magnitude: float = 0.0      # below this value the point is not evaluable
    group_by_hour: bool = False     # estimate the scale separately for each hour of the day


def _mad(w: np.ndarray) -> float:
    return float(np.median(np.abs(w - np.median(w))))


def _change(x: pd.Series, p: Params) -> pd.Series:
    prev = x.shift(p.season)
    if p.transform == "log_ratio":
        valid = (x > 0) & (prev > 0)
        return np.log(x.where(valid) / prev.where(valid))
    if p.transform == "difference":
        return x - prev
    raise ValueError("transform must be 'log_ratio' or 'difference'")


def _robust_z(r: pd.Series, p: Params) -> pd.DataFrame:
    past = r.shift(1)
    roll = past.rolling(p.window, min_periods=1)
    return pd.DataFrame({
        "n_history": past.rolling(p.window, min_periods=1).count(),
        "median": roll.median(),
        "mad": past.rolling(p.window, min_periods=1).apply(lambda w: _mad(w[~np.isnan(w)]), raw=True),
    })


def score(series: pd.Series, p: Params) -> pd.DataFrame:
    """Score every point. Returns value, reference, change, z, severity and method."""
    x = series.sort_index().astype(float)
    r = _change(x, p)

    # Work by position: local timestamps repeat when clocks go back in October.
    r_pos = pd.Series(r.values)
    if p.group_by_hour:
        hours = np.asarray(x.index.hour)
        stats = pd.concat([_robust_z(g, p) for _, g in r_pos.groupby(hours)]).sort_index()
    else:
        stats = _robust_z(r_pos, p)

    out = pd.DataFrame({"value": x, "reference": x.shift(p.season), "change": r})
    for c in stats.columns:
        out[c] = stats[c].values
    usable = (out["n_history"] >= p.min_history) & (out["mad"] > 0)
    out["z"] = np.where(usable, K * (out["change"] - out["median"]) / out["mad"], np.nan)

    rel = (out["value"] / out["reference"] - 1).abs()
    severity = np.select(
        [usable & (out["z"].abs() > p.z_high), usable & (out["z"].abs() > p.z_medium),
         ~usable & (rel > p.fallback_medium), ~usable & (rel > p.fallback_low)],
        ["high", "medium", "medium", "low"], default="none")
    method = np.where(usable, "robust_z", "relative_change")
    if p.transform == "difference":  # relative change is meaningless around zero prices
        severity = np.where(~usable, "not_evaluable", severity)
        method = np.where(~usable, "none", method)

    no_data = out["change"].isna() | (out["value"].abs() < p.min_magnitude)
    out["severity"] = np.where(no_data, "not_evaluable", severity)
    out["method"] = np.where(no_data, "none", method)
    return out.drop(columns=["median", "mad"])


PRESETS = {
    "demand": Params(season=7, transform="log_ratio"),
    "generation": Params(season=7, transform="log_ratio", min_magnitude=1000.0),
    "spot_price": Params(season=168, transform="difference", group_by_hour=True),
}
