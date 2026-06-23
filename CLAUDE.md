# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A small Flask web app that renders options gamma-exposure / SpotGamma-style dealer-positioning levels and a TradingView-importable level string. Two interchangeable upstreams, toggled in the UI with a Source dropdown (also deep-linkable via `?source=menthorq` or `?source=gexapi`):

- **MenthorQ** (default) — precomputed daily levels from `https://api.menthorq.io/getDailyLevels`. Six level types: `gamma_levels` (default), `gamma_levels_intraday`, `gamma_scalping`, `gamma_scalping_intraday`, `blindspots`, `swing_levels`. Requires `MENTHORQ_API_KEY`.
- **GEX API** — raw delayed option chains from `https://gex-api-xiq6cw7ltq-ue.a.run.app/delayed_options/{sym}`. Parses OCC option symbols and computes levels in-process. No key.

Single Flask process, no DB, no auth, no cache. Runs in one Docker container.

## Run / develop

```bash
# Build + run (port 5181 → container 8000)
docker build -t gex-viewer .
docker rm -f gex-viewer 2>/dev/null
docker run -d --name gex-viewer -p 5181:8000 \
  -e MENTHORQ_API_KEY="$MENTHORQ_API_KEY" \
  gex-viewer

# Logs / restart / teardown
docker logs gex-viewer
docker restart gex-viewer
docker rm -f gex-viewer

# Fast iteration without rebuild (mount source, autoreload):
docker run --rm -p 5181:8000 -v "$PWD":/app \
  -e FLASK_DEBUG=1 -e MENTHORQ_API_KEY="$MENTHORQ_API_KEY" \
  gex-viewer python app.py
```

Visit `http://localhost:5181/<SYMBOL>` — `/ES`, `/NQ`, `/SPY`, etc. Default page is `/ES`. No tests, no linter, no CI.

## Env vars

| Var                | Default        | Used for                                                |
| ------------------ | -------------- | ------------------------------------------------------- |
| `MENTHORQ_API_KEY` | `""`           | MenthorQ `X-API-KEY` header. Missing → 500 on `/api/levels/*` with a clear message; GEX API mode still works. |
| `MENTHORQ_USER_ID` | `gex-viewer`   | `user_id` query param (the API requires it; any value works). |

`.env.example` lives in the repo; `.env` is gitignored.

## MenthorQ source (`/api/levels/<sym>?level_type=...`)

- Response shape: `{ ticker, ticker_mq, level_type, kind, date, level_values: [{name, value}, ...], spot, tv_string }`.
- `ticker_mq` is **already in TV format** (e.g. `ES1!`, `NQ1!`) — used directly in the TV string. This means the index↔futures basis gap that affects GEX API mode does NOT apply here.
- `spot` is derived locally as `mid(1D Min, 1D Max)` when both are present (only the `gamma_*` level types have them); otherwise `null`.
- Level name conventions vary by type: `gamma_*` → Call Resistance / Put Support / HVL / 1D Min/Max / *_0DTE variants / `GEX 1`..`GEX 10`. `blindspots` → `BL 1`..`BL 10`. `swing_levels` → `UB MM-DD` / `LB MM-DD` / `RT MM-DD`.
- Unknown `level_type` → 400. Upstream error / no key → 502 / 500 with `message`.

## GEX API source (`/api/gex/<sym>`)

Behaves like the original app shipped:

- **Index symbols need an underscore prefix upstream**: `_SPX`, `_NDX`, `_RUT`, `_VIX`, `_DJX`, `_XSP`. Raw `SPX` returns `{"error": true}`.
- **Futures aliasing**: user-facing `ES`/`NQ`/`RTY`/`VX`/`YM` map to the corresponding cash index (`_SPX`/`_NDX`/...) via the `ALIAS` dict — that's where option liquidity lives. Literal `ES` would otherwise resolve to Eversource Energy.
- **TV ticker mapping**: `TV_TICKER` maps user-facing symbol → the string used in the TradingView export header (`$ES1!`, etc.). Front-month continuous contract convention.
- **Fallback chain** in `resolve_gex()`: alias → raw → if upstream errors and symbol isn't already underscored, retry with `_` prefix.
- **GEX per row**: `gamma × open_interest × 100 × sign(C=+1, P=−1)`. The `× 100` is the equity-options contract multiplier; it cancels out of relative comparisons but is kept so magnitudes look like SpotGamma's.
- **Call Resistance / Put Support**: strike with max/min *net* GEX across the chosen scope. Not the strike with biggest call/put OI in isolation.
- **HVL (gamma flip)**: strike where the *cumulative* net GEX (sorted ascending by strike) crosses zero. Computed twice: across all expiries (`hvl`) and for 0DTE only (`hvl_0dte`).
- **0DTE**: contracts whose `expiry == today` (server time, UTC). If today has no expiry, falls back to the nearest future expiry — intentional so weekends/holidays still produce usable levels.
- **1D Min/Max**: `spot ± spot × iv30/100 × sqrt(1/365)`. A 1-sigma daily move from the 30-day implied vol field. Approximation, not from a SpotGamma model.

### Known gap (GEX API mode only): index-to-futures basis is not applied

Levels are computed on SPX/NDX strikes and printed under `$ES1!`/`$NQ1!`. The cash-vs-futures basis (typically +5 to +20 SPX points, +100 to +200 NDX points) is **not** added. If a user reports their TradingView levels look "off by a constant" while in GEX API mode — that's why. MenthorQ mode does not have this issue. Two acceptable fixes if asked:

1. Manual `basis` input in the UI (persisted per-symbol in localStorage), applied to every strike in the TV string and chart.
2. Auto-basis via `yfinance` (`ES=F` − `^GSPC`, `NQ=F` − `^NDX`) — free, ~15min delayed, no key.

## OCC symbol parser (GEX API mode only)

`OCC_RE = ^([A-Z]+)(\d{6})([CP])(\d{8})$` — root letters, `YYMMDD`, `C`/`P`, strike in thousandths of a dollar. If a new symbol returns rows with `option` strings that don't match, `parse_occ()` silently drops them — symptom is `rows: 0` despite a non-error upstream response.

## Architecture

- `app.py` — Flask. Both sources are in this one file, in clearly labelled sections. Single process, no DB, no cache. Each request hits the relevant upstream fresh (both are already cached/delayed on their side).
- `templates/index.html` — single page, vanilla JS + Plotly from CDN. Two render paths gated by a `MODE` variable:
  - **MenthorQ**: clean level-map (horizontal lines per named level on a strike axis, labelled).
  - **GEX API**: histogram by strike with expiry filter, view modes, orientation toggle, gamma flip indicator. Auto-zoom keeps strikes containing 99% of `|GEX|` mass to suppress wide-tail zero-OI strikes that would otherwise flatten the chart.

## Screenshotting the running app

```bash
# macOS/Docker Desktop: use host.docker.internal (the --network host pattern from older docs is unreliable here)
docker run --rm -v /tmp:/out zenika/alpine-chrome:latest \
  --no-sandbox --headless --disable-gpu --hide-scrollbars \
  --window-size=1500,1100 --virtual-time-budget=20000 \
  --screenshot=/out/page.png http://host.docker.internal:5181/ES

# Add ?source=gexapi to capture the histogram view instead of the level map.
```

`--virtual-time-budget` is required — without it, the screenshot fires before the API fetch + Plotly CDN load complete and you get a blank "loading…" page.
