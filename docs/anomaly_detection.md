# Anomaly detection method

## Purpose

Flag values of demand, generation or spot price that are unusual **compared with the series' own recent history**. The detector tells *what* is unusual and *how much*. It does not explain *why*: causes need other data (weather, outages, calendar).

## Reference: the same period one week earlier

Each point is compared with the same point one season earlier:

| Series | Resolution | Season | Reference |
|---|---|---|---|
| Demand | daily | 7 days | same weekday, previous week |
| Generation (per technology) | daily | 7 days | same weekday, previous week |
| Spot price | hourly | 168 hours | same hour, same weekday, previous week |

Comparing with the previous week removes the weekly pattern (weekends, working days) without having to model it.

## Change measured

- **Log-ratio** for strictly positive series (demand, generation): `r_t = ln(x_t / x_{t-s})`. A halving and a doubling have the same magnitude, and the measure does not depend on the size of the series.
- **Difference** for prices: `r_t = x_t - x_{t-s}`. Spot prices can be zero or negative during solar hours, so ratios are undefined. For prices, the scale is estimated separately for each hour of the day, because midday prices are far more volatile than night prices.

## Score: modified z-score

```
z_t = 0.6745 * (r_t - median(r)) / MAD(r)
```

- `median(r)` and `MAD(r)` (median absolute deviation) are computed over the **previous 52 changes**, **excluding the point being scored**. An outlier therefore cannot inflate its own scale and hide.
- The constant 0.6745 makes the MAD a consistent estimator of the standard deviation under normality, so `z` reads like a classic z-score.
- Reference: Iglewicz, B. and Hoaglin, D. (1993), *How to Detect and Handle Outliers*.

## Severity

| Severity | Condition |
|---|---|
| `high` | \|z\| > 5.0 |
| `medium` | \|z\| > 3.5 (threshold recommended by Iglewicz & Hoaglin) |
| `low` | Fallback only: short history and relative change > 35 % |
| `not_evaluable` | No reference value, or the value is below the minimum magnitude |
| `none` | Evaluated and not unusual |

### Fallback with short history

If fewer than **8** past changes are available, or the MAD is 0, the z-score is not reliable. The relative change `|x_t / x_{t-s} - 1|` is used instead: above 35 % it is `low`, above 70 % it is `medium`. It is one level lower than the z-score would give, because it is a weaker criterion. Prices do not use this fallback (a relative change around zero is meaningless) and are reported as `not_evaluable`.

### Not evaluable is reported explicitly

When a point cannot be checked, it is labelled `not_evaluable` instead of being silently skipped. Otherwise "no anomaly" and "could not be checked" would look the same.

Generation values below **1,000 MWh/day** are not evaluated: for very small technologies, relative changes are mostly noise.

## How to read a result

- `value`: the observed value.
- `same_period_last_week`: the reference value.
- `z`: how many robust standard deviations the change is away from the usual change. The sign shows the direction (negative is lower than usual).
- `method`: `robust_z` or `relative_change` (fallback).

## Known limitations

1. **Rebound effect.** Because the reference is the previous week, a holiday or an outage also makes the *following* week look anomalous, in the opposite direction. Example: if a reactor is offline on 7 October, normal output on 14 October is flagged as unusually high.
2. **Very stable series give huge z-scores.** Nuclear output barely changes, so the MAD is tiny and a real change can give |z| of 20 to 40. The severity is still meaningful, but z values are not comparable across series.
3. **No calendar.** Holidays are detected as anomalies (often correctly), but the detector does not know they are holidays.
4. **Thresholds are not calibrated.** 3.5 / 5.0, 35 % / 70 % and the 8-change minimum are starting points, to be tuned to how many alerts someone can review.
