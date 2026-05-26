# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A small Flask web app that pulls delayed option-chain data from `https://gex-api-xiq6cw7ltq-ue.a.run.app/delayed_options/{sym}`, parses the OCC option symbols, computes SpotGamma-style dealer-positioning levels (Call Resistance, Put Support, HVL/gamma flip, 0DTE variants, top-N GEX strikes, 1D Min/Max from iv30), and renders a Plotly chart plus a TradingView-importable string. Runs in a single Docker container.

## Run / develop

```bash
# Build + run (port 5181 → container 8000)
docker build -t gex-viewer .
docker rm -f gex-viewer 2>/dev/null
docker run -d --name gex-viewer -p 5181:8000 gex-viewer

# Logs / restart / teardown
docker logs gex-viewer
docker restart gex-viewer
docker rm -f gex-viewer

# Fast iteration without rebuild (mount source, autoreload):
docker run --rm -p 5181:8000 -v "$PWD":/app -e FLASK_DEBUG=1 \
  gex-viewer python app.py
```

Visit `http://localhost:5181/<SYMBOL>` — symbol is path-driven, e.g. `/ES`, `/NQ`, `/SPY`. No tests, no linter, no CI.

## Symbol resolution (important, non-obvious)

The upstream API has quirks that the app papers over:

- **Index symbols need an underscore prefix upstream**: `_SPX`, `_NDX`, `_RUT`, `_VIX`, `_DJX`, `_XSP`. Raw `SPX` returns `{"error": true}`.
- **Futures aliasing**: user-facing `ES`/`NQ`/`RTY`/`VX`/`YM` are mapped to the corresponding cash index (`_SPX`/`_NDX`/...) because that's what has option liquidity. The `ALIAS` dict in `app.py` controls this. Note: literal `ES` on the upstream API resolves to Eversource Energy (stock), NOT S&P futures — the alias overrides that.
- **TV ticker mapping**: `TV_TICKER` maps user-facing symbol → the string used in the TradingView export header (`$ES1!`, `$NQ1!`, etc.). Front-month continuous contract convention.
- **Fallback chain** (`resolve()` in `app.py`): try alias → try raw → if upstream errors and symbol isn't already underscored, retry with `_` prefix. This handles unknown indexes without needing to extend `ALIAS`.

## Level computation conventions

- **GEX per row**: `gamma × open_interest × 100 × sign(C=+1, P=−1)`. The `× 100` is the equity-options contract multiplier; it cancels out of relative comparisons but is kept so the magnitudes look like SpotGamma's.
- **Call Resistance / Put Support**: strike with max/min net GEX across the chosen scope. Not the strike with biggest call/put OI in isolation — it's net.
- **HVL (gamma flip)**: strike where the *cumulative* net GEX (sorted ascending by strike) crosses zero. Computed twice: across all expiries (`hvl`) and for 0DTE only (`hvl_0dte`).
- **0DTE**: contracts whose `expiry == today` (server time, UTC). If today has no expiry, falls back to the nearest future expiry — this is intentional so weekends/holidays still produce usable levels.
- **1D Min/Max**: `spot ± spot × iv30/100 × sqrt(1/365)`. A 1-sigma daily move from the 30-day implied vol field. Approximation, not derived from a SpotGamma model.

## Known gap: index-to-futures basis is not applied

Levels are computed on SPX/NDX strikes and printed under `$ES1!`/`$NQ1!`. The cash-vs-futures basis (typically +5 to +20 SPX points, +100 to +200 NDX points) is **not** added. If a user reports their TradingView levels look "off by a constant" — that's why. Two acceptable fixes if asked:
1. Manual `basis` input in the UI (persisted per-symbol in localStorage), applied to every strike in the TV string and chart.
2. Auto-basis via `yfinance` (`ES=F` − `^GSPC`, `NQ=F` − `^NDX`) — free, ~15min delayed, no key.

## OCC symbol parser

`OCC_RE = ^([A-Z]+)(\d{6})([CP])(\d{8})$` — root letters, `YYMMDD`, `C`/`P`, strike in thousandths of a dollar. Has matched all upstream symbols seen so far (SPX, NDX, SPY, QQQ, ES-as-stock, etc.). If a new symbol returns rows with `option` strings that don't match, `parse_occ()` silently drops them — symptom is `rows: 0` despite a non-error upstream response.

## Architecture (small enough to skim, but worth flagging)

- `app.py` — Flask app. Three concerns intentionally co-located: upstream fetch + symbol resolution, OCC parsing + row shaping, level computation. Single-process, no DB, no cache. Each request hits the upstream API fresh (it's already cached/delayed on their side).
- `templates/index.html` — single page, vanilla JS + Plotly from CDN. All chart filtering and rendering happens client-side from the `/api/gex/<sym>` JSON. The expiry dropdown defaults to the nearest future expiry, labeled `0DTE — YYYY-MM-DD`; `ALL` is appended at the bottom.
- Auto-zoom on the x-axis keeps the strikes containing 99% of `|GEX|` mass — important because raw index chains include wide-tail strikes out to 20000 with zero OI that would otherwise flatten the chart.

## Screenshotting the running app (for verifying chart changes)

```bash
docker run --rm --network host -v /tmp:/out zenika/alpine-chrome:latest \
  --no-sandbox --headless --disable-gpu --hide-scrollbars \
  --window-size=1500,1100 --virtual-time-budget=20000 \
  --screenshot=/out/page.png http://localhost:5181/ES
```

`--virtual-time-budget` is required — without it, the screenshot fires before the `/api/gex/...` fetch + Plotly CDN load complete and you get a blank "loading…" page.
