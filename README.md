# grid-anomaly-agent

Ask questions in plain language about the Spanish electricity system.

`grid-anomaly-agent` downloads public data from Red Eléctrica's [REData API](https://www.ree.es/en/apidatos) (no key needed) into a local SQLite database, and an LLM agent answers questions by calling tools:

- **`run_sql`**: read-only SQL over the database, behind several guardrails
- **`detect_anomalies`**: unusual demand, generation or price values compared with each series' own history, using robust statistics
- **`list_tables`**: which data is available and for which dates
- **`read_document`**: the project documentation in [`docs/`](docs/) (method, severities, limitations, data dictionary), so the agent explains results from the documentation instead of improvising

It works with **any OpenAI-compatible endpoint** (Groq, OpenAI, vLLM, Ollama, LM Studio…). The agent loop is written from scratch, with no agent framework, so every step is visible.

```
question ──► LLM ──tool call──► Toolbox ──► SQLite (read-only)
              ▲                    │    └──► docs/*.md
              └─── tool result ────┘          (repeat until the LLM answers, max N steps)
```

## Data

Full description in [`docs/data_dictionary.md`](docs/data_dictionary.md).

| Table | Content | Resolution |
|---|---|---|
| `spot_price_hourly` | Day-ahead spot market price (EUR/MWh) | hourly |
| `pvpc_hourly` | Regulated retail price PVPC (EUR/MWh) | hourly |
| `demand_daily` | Electricity demand (MWh) | daily |
| `generation_daily` | Peninsular generation by technology (MWh) | daily |

Timestamps are stored in UTC and also as local time (Europe/Madrid).

## Quick start

```bash
pip install -e ".[dev]"

# 1. Download two years of data (a minute or two)
grid-anomaly-agent ingest --start 2024-10-01 --end 2026-10-19

# 2. Point it to an LLM: copy .env.example to .env and fill it in (loaded automatically)
cp .env.example .env
grid-anomaly-agent ask "Which day of 2025 had the highest electricity demand?" --trace
grid-anomaly-agent ask "Were there any unusual spot prices in September 2026?"

# 3. Anomaly detection also runs on its own, without an LLM
grid-anomaly-agent detect --series demand --start 2025-10-01
```

## Anomaly detection

Full description in [`docs/anomaly_detection.md`](docs/anomaly_detection.md). This is also the document the agent reads to explain its results.

Each point is compared with **the same point one season earlier**: the same weekday last week for daily data, or the same hour last week for hourly prices.

| | Change used | Why |
|---|---|---|
| Demand, generation | log-ratio `ln(x_t / x_{t-7})` | removes the weekly pattern without modelling it, and halving and doubling weigh the same |
| Spot price | difference `x_t − x_{t-168}`, scale estimated per hour of day | prices can be zero or negative (solar hours), so ratios are undefined |

The change is scored with the **modified z-score** (Iglewicz & Hoaglin, 1993) against the trailing history of changes:

```
z_t = 0.6745 · (r_t − median(r)) / MAD(r)
```

- **Robust estimators** (median and MAD), computed **excluding the point itself**, so an outlier cannot hide by inflating its own scale.
- **Severity:** `high` when |z| > 5, `medium` when |z| > 3.5 (the threshold recommended by Iglewicz & Hoaglin).
- **Fallback with short history:** with fewer than 8 past changes, or MAD = 0, a relative-change rule is used instead (> 35 % → `low`, > 70 % → `medium`). It is one level lower because it is less reliable.
- **`not_evaluable`:** when there is no reference or the magnitude is too small to be meaningful, the point is flagged explicitly. "No anomaly" and "could not be checked" are never the same thing.

All thresholds live in `anomaly.Params` and are starting points to calibrate.

## Example results

Real runs on data from October 2024 to October 2026. Anomalies flagged independently in different series often describe the same event, which can be checked with a SQL query.

### Holidays in daily demand

```
$ grid-anomaly-agent detect --series demand --start 2025-10-01 --top 6
demand: 384 points scored, by severity {'none': 362, 'medium': 16, 'high': 6}

  2025-12-25       high   value=610017.1     last week=780182.3     z=-8.23  (robust_z)
  2025-12-08       high   value=651584.4     last week=790477.9     z=-8.22  (robust_z)
  2026-05-08       high   value=678174.0     last week=557689.9     z=7.01  (robust_z)
  2026-01-08       high   value=864846.5     last week=614015.7     z=6.62  (robust_z)
  2026-04-03       high   value=551660.2     last week=699970.2     z=-6.5  (robust_z)
  2026-05-01       high   value=557689.9     last week=662357.2     z=-5.17  (robust_z)
```

The detector finds the national holidays (Christmas, 8 December, Good Friday, 1 May) without being told about them. 8 May and 8 January are rebounds from the holidays one week earlier (see *Things to consider*).

### A cloudy Sunday: solar halves and midday prices jump (28 September 2026)

Two separate anomalies, solar generation (`high`, z = −5.2) and the spot price (`high`, z = 7.7 at 13:00), are the same event:

| Date | Solar PV (MWh) | Wind (MWh) | Mean spot price 11–16 h (EUR/MWh) |
|---|---|---|---|
| Sun 21 Sep 2026 | 224,813 | 108,607 | 5.3 |
| Sun 28 Sep 2026 | 116,638 | 128,169 | **169.6** |

Without the midday solar surplus, prices no longer collapse to near zero.

### High demand served by combined cycles (4–5 September 2026)

Demand (`medium`, z ≈ 4.5) and combined-cycle generation (`medium`, z ≈ 4) rise together, and the 18:00 price is flagged `high`:

| Date | Demand (MWh) | Combined cycle (MWh) |
|---|---|---|
| Fri 28 Aug | 713,734 | 103,481 |
| Fri 4 Sep | 836,733 | 225,416 |
| Sat 29 Aug | 633,643 | 86,310 |
| Sat 5 Sep | 756,501 | 181,849 |

The data alone does not tell the cause: heat, or the reference week still being in the August holidays.

### A nuclear unit offline (7–8 October 2026)

| Date | Nuclear (MWh) | Mean MW |
|---|---|---|
| 6 Oct | 167,337 | 6,972 |
| 7 Oct | 142,173 | 5,924 |
| 8 Oct | 142,968 | 5,957 |
| 12 Oct | 167,953 | 6,998 |

About 1,050 MW is missing for two days, roughly one reactor, then output recovers. The detector flags 7–8 October as `high` (z ≈ −28). It also flags 14–15 October (z ≈ +40), but output those days is normal: they are compared with the outage week.

## Things to consider

This is an example project. Before using the detector for anything serious:

- **Rebounds after an event.** The reference is the same period one week earlier, so a holiday or an outage also makes the *following* week look anomalous (8 May, 8 January and 14–15 October above). A reference built from several previous weeks, a holiday calendar, or excluding points already flagged from the history would remove most of them.
- **Very stable series give huge z-scores.** Nuclear output barely changes, so its MAD is tiny and any real change gives |z| of 20–40. The severity is still right, but the z value is not comparable across series.
- **Causes need other data.** The detector says *what* is unusual, not *why*. Explaining the 4–5 September demand would need temperatures or a calendar.
- **Thresholds are not calibrated.** The values in `anomaly.Params` (3.5 / 5.0, the 35 % / 70 % fallback, 8 past changes) are reasonable starting points. They should be tuned to how many alerts someone can actually review.
- **The agent depends on the model.** See *Tested with* below: two models given the same tools and data reach different conclusions on the same question.
- **Arithmetic belongs in SQL.** In early tests a model computed a percentage by hand and got it badly wrong. The system prompt now requires every derived number to be computed in the query, but models still sometimes round or approximate in the text.

## Tested with

Real runs on [Groq](https://console.groq.com) (OpenAI-compatible API, free tier), October 2026.

| Question | gpt-oss-120b | Qwen 3.8 27B |
|---|---|---|
| Which day of 2025 had the highest demand? | ✅ 2 July 2025, 861,179 MWh | ✅ same |
| Unusual spot prices in late September 2026, and solar on those days? | ✅ finds the 28 Sep spikes and solar values, but does not compare with the previous week | ✅ compares with the previous week in SQL: solar PV halved |
| Share of wind in January 2026? | ❌ **81.98 %** before the prompt fix (division done by the model) · ✅ 33.66 % after | ✅ 33.7 % before · ✅ 33.66 % after |
| Average spot price in 2019 (no data) | ✅ says data starts in October 2024 | ✅ same, and offers an alternative |
| Is the nuclear anomaly on 14 Oct 2026 real? | ⚠️ reads the documentation and mentions the rebound effect, but concludes it is a real anomaly | ✅ false alarm: combines the detector, the documentation and a SQL query showing the outage the week before |

Observations:
- Both models recover from their own mistakes because errors are returned as tool results: wrong table names, a technology called `'Solar'` instead of `'Solar photovoltaic'`, malformed tool arguments.
- Qwen tends to guess table names before calling `list_tables`. The prompt asks for `list_tables` first, but it does not always comply.
- The free tier has low per-minute token limits. The client retries on HTTP 429, honouring `Retry-After`.

### Example: the agent explains a false alarm (Qwen 3.8)

```
$ grid-anomaly-agent ask "Nuclear generation was flagged as a high-severity anomaly on 14 October 2026.                  Is it a real anomaly or a false alarm? Explain briefly how the detector decided." --trace

[detect_anomalies] {"series":"generation","technology":"Nuclear","start":"2026-10-14","end":"2026-10-14","min_severity":"high"}
  -> {"listed": [{"time": "2026-10-14", "value": 167851.6, "same_period_last_week": 142172.8, "z": 41.26, "severity": "high"}]}
[read_document] {"name":"anomaly_detection"}
[run_sql] {"query":"SELECT ts_local, value FROM generation WHERE ..."}
  -> {"error": "Query failed: no such table: generation"}
[list_tables] {}
[run_sql] {"query":"SELECT date, generation_mwh FROM generation_daily WHERE technology='Nuclear' AND date BETWEEN '2026-10-01' AND '2026-10-14' ORDER BY date"}
  -> {"rows": [["2026-10-06", 167337.4], ["2026-10-07", 142172.8], ["2026-10-08", 142967.6], ["2026-10-09", 151293.2], ...]}

Verdict: false alarm — a rebound effect, not a real anomaly.

14 Oct 2026 (167,851.6 MWh) is essentially identical to the normal level: 12 Oct 167,953.2, 13 Oct 167,912.0.
The reference day, 7 Oct (142,172.8 MWh), was the low one: output dropped sharply on 7–8 Oct and then
ramped back up over 9–12 Oct, consistent with a reactor outage.

Because the detector compares each day with the same weekday one week earlier, the outage on 7 Oct makes
the following week look unusually high. This is the documented rebound effect (Known limitation #1).
Nuclear output is very stable, so the MAD is tiny and even a return to normal produces a huge |z|
(Known limitation #2).
```

(Answer shortened; tool outputs abbreviated.)

## SQL guardrails

SQL written by the model goes through four independent layers:

1. **Static validation:** a single statement, `SELECT` or `WITH` only, no write or `PRAGMA`/`ATTACH` keywords, and comments stripped before checking.
2. **Read-only connection:** SQLite opened with `mode=ro`.
3. **SQLite authorizer:** only read operations are allowed at engine level.
4. **Limits:** 200 rows per result and a 5-second time limit.

Rejected queries are returned to the model as an error message, so it can correct itself instead of failing.

## Tests

```bash
pytest
```

The agent is tested with a scripted fake LLM: tool results reaching the model, rejected SQL, unknown tools, document reading and the step limit. No network or model is needed.

## Project layout

```
grid_anomaly_agent/
  redata.py    REData client (calendar-month chunks, retries)
  store.py     SQLite schema, loading and read-only access
  sqlguard.py  SQL guardrails
  anomaly.py   robust anomaly detection
  tools.py     tool schemas and implementations
  llm.py       minimal OpenAI-compatible client
  agent.py     tool-calling loop
  cli.py       command line interface
docs/
  anomaly_detection.md   method, severities and known limitations (read by the agent)
  data_dictionary.md     tables, columns, units and caveats (read by the agent)
tests/
```

## Data source and license

Data: Red Eléctrica (REE), REData API, under REE's [terms of use](https://www.ree.es/en/apidatos).
Code: MIT License.
