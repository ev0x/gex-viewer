# gex-viewer

A small Flask + Plotly web app that visualises options gamma exposure (GEX) and produces a TradingView-importable level string in the SpotGamma vocabulary (Call Resistance, Put Support, HVL, 1D Min/Max, 0DTE variants, top-10 GEX strikes).

Pulls delayed option chains from a public endpoint, parses OCC option symbols, and computes the levels in-process. No API key, no database, no auth.

## Quick start

```bash
docker build -t gex-viewer .
docker run -d --name gex-viewer -p 5181:8000 gex-viewer
open http://localhost:5181/ES
```

Stop and remove:

```bash
docker rm -f gex-viewer
```

## Usage

Visit `http://localhost:5181/<SYMBOL>`. The symbol is path-driven:

| You type | What it fetches    | TradingView label |
| -------- | ------------------ | ----------------- |
| `ES`     | `_SPX`             | `$ES1!`           |
| `NQ`     | `_NDX`             | `$NQ1!`           |
| `RTY`    | `_RUT`             | `$RTY1!`          |
| `VX`     | `_VIX`             | `$VX1!`           |
| `YM`     | `_DJX`             | `$YM1!`           |
| `SPX`    | `_SPX`             | `$SPX`            |
| `SPY`    | `SPY` (stock)      | `$SPY`            |
| `QQQ`    | `QQQ` (stock)      | `$QQQ`            |
| any other | tried as-is, then with `_` prefix if upstream returns an error | passthrough |

The expiry dropdown defaults to the nearest expiry (labelled `0DTE — YYYY-MM-DD`); `ALL` is at the bottom. Click **Copy** under the TradingView import string to put the levels on your clipboard.

## Running without Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py        # http://localhost:8000
```

## Known limitations

- **Index-to-futures basis is not applied.** Strikes are SPX/NDX values printed under `$ES1!`/`$NQ1!`. The cash-vs-futures basis (a handful of points for ES, ~100–200 for NQ) is not added. If your TradingView levels look off by a constant offset, that's why.
- **Delayed data only.** The upstream endpoint is delayed (~15 min). Don't use this for execution.
- **`1D Min/Max` is an approximation** — `spot ± 1σ` from the `iv30` field on the underlying, not from a vol surface model.

## Project layout

- `app.py` — Flask app: upstream fetch, OCC parsing, GEX/level computation
- `templates/index.html` — single-page UI, vanilla JS + Plotly from CDN
- `Dockerfile` — `python:3.12-slim` + gunicorn
- `requirements.txt` — flask, requests, gunicorn

## License

MIT — see [LICENSE](LICENSE).
