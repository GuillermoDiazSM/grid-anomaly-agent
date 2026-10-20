# Data dictionary

All data comes from Red Eléctrica (REE), the Spanish transmission system operator, through its public REData API (https://www.ree.es/en/apidatos). No API key is needed.

## Tables

### `spot_price_hourly`

Day-ahead market (OMIE) spot price for the Spanish zone.

| Column | Type | Meaning |
|---|---|---|
| `ts_utc` | text | Hour start in UTC, `YYYY-MM-DDTHH:MMZ` (primary key) |
| `ts_local` | text | Hour start in Europe/Madrid, `YYYY-MM-DD HH:MM` |
| `price_eur_mwh` | real | Price in EUR/MWh |

- The source publishes 15-minute prices. They are averaged to hourly values.
- Prices can be **zero or negative**, typically at midday on sunny days with a lot of solar generation.
- When clocks go back in October, one local hour appears twice. Use `ts_utc` to tell them apart.

### `pvpc_hourly`

PVPC (*Precio Voluntario para el Pequeño Consumidor*): the regulated retail price for households on the regulated tariff, in EUR/MWh. Same columns as `spot_price_hourly`. It includes energy, network charges and other regulated costs, so it is usually well above the spot price.

### `demand_daily`

| Column | Type | Meaning |
|---|---|---|
| `date` | text | Day, `YYYY-MM-DD` (Europe/Madrid) |
| `demand_mwh` | real | Electricity demand during the day, in MWh |

This is REE's *Demand evolution* series with its default geographic scope. Demand is lower at weekends and on public holidays, and higher during cold spells and heat waves.

### `generation_daily`

Peninsular generation (mainland Spain, excluding the Balearic and Canary Islands, Ceuta and Melilla) by technology.

| Column | Type | Meaning |
|---|---|---|
| `date` | text | Day, `YYYY-MM-DD` (Europe/Madrid) |
| `technology` | text | Technology name, exactly as published by REE |
| `generation_mwh` | real | Energy generated during the day, in MWh |

Technologies: `Wind`, `Solar photovoltaic`, `Thermal solar`, `Hydro`, `Nuclear`, `Combined cycle`, `Cogeneration`, `Coal`, `Other renewables`, `Renewable waste`, `Non-renewable waste`, `Steam turbine`, `Fuel + Gas`.

- Use the exact names above in SQL: for example `'Solar photovoltaic'`, not `'Solar'`.
- The REE "Total generation" series is not stored. To get the total, sum all technologies for the day.
- Some technologies are very small or appear only on some dates.

## Units and conversions

- 1 GWh = 1,000 MWh. A daily total in MWh divided by 24 gives the average power in MW.
- A large nuclear reactor is about 1,000 MW, that is, about 24,000 MWh per day.

## Coverage

Coverage depends on the dates passed to `grid-anomaly-agent ingest`. Use the `list_tables` tool to see the first and last date of each table.
