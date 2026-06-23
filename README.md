# gex-viewer

A small Flask + Plotly web app that shows options gamma-exposure (GEX) and SpotGamma-style dealer-positioning levels (Call Resistance, Put Support, HVL/gamma flip, 1D Min/Max, 0DTE variants, top-N GEX strikes), and produces a TradingView-importable level string.

Two interchangeable data sources, toggled in the UI:

- **MenthorQ** — precomputed daily levels (`gamma_levels`, `gamma_levels_intraday`, `gamma_scalping`, `gamma_scalping_intraday`, `blindspots`, `swing_levels`). Renders a clean level-map chart. Ticker comes back already in TV format (`$ES1!`, `$NQ1!`), so no index↔futures basis fix is needed. **Requires an API key.**
- **GEX API** — raw delayed option chain from a public endpoint. Renders the full per-strike GEX histogram with expiry filter, view modes (net / calls-vs-puts / raw γ·OI), orientation toggle, and a gamma-flip indicator. No key required.

## Quick start

```bash
docker build -t gex-viewer .
docker run -d --name gex-viewer -p 5181:8000 \
  -e MENTHORQ_API_KEY=your_key_here \
  gex-viewer
open http://localhost:5181/ES
```

The MenthorQ key is only needed if you want the MenthorQ source — the GEX API source works without it. Get a key at <https://menthorq.io>.

Stop and remove:

```bash
docker rm -f gex-viewer
```

## Usage

Visit `http://localhost:5181/<SYMBOL>`. The symbol is path-driven. Use the **Source** dropdown to pick which feed you want, and deep-link a mode with `?source=menthorq` or `?source=gexapi`.

### MenthorQ mode (default)

- Defaults to the `gamma_levels` level type. Switch level types from the dropdown.
- Pass any ticker the API supports: `SPY`, `ES`, `NQ`, `RTY`, etc.
- For futures, MenthorQ returns the futures-priced levels and `ticker_mq` like `ES1!` — directly usable in the TV string.

### GEX API mode

Same upstream the original app shipped with. Symbol aliases:

| You type | What it fetches    | TradingView label |
| -------- | ------------------ | ----------------- |
| `ES`     | `_SPX`             | `$ES1!`           |
| `NQ`     | `_NDX`             | `$NQ1!`           |
| `RTY`    | `_RUT`             | `$RTY1!`          |
| `VX`     | `_VIX`             | `$VX1!`           |
| `YM`     | `_DJX`             | `$YM1!`           |
| `SPX`    | `_SPX`             | `$SPX`            |
| `SPY`    | `SPY` (stock)      | `$SPY`            |
| any other | tried as-is, then with `_` prefix if upstream returns an error | passthrough |

The expiry dropdown defaults to the nearest expiry (labelled `0DTE — YYYY-MM-DD`); `ALL` is at the bottom.

Click **Copy** under the TradingView import string to put the levels on your clipboard.

## Configuration

| Env var              | Default      | Purpose                                                  |
| -------------------- | ------------ | -------------------------------------------------------- |
| `MENTHORQ_API_KEY`   | _(empty)_    | Required for the MenthorQ source. Sent as `X-API-KEY`.   |
| `MENTHORQ_USER_ID`   | `gex-viewer` | Passed as `user_id` query param (any non-empty value works). |

Copy `.env.example` to `.env` and fill it in for local development. `.env` is gitignored.

## Running without Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export MENTHORQ_API_KEY=your_key_here
python app.py        # http://localhost:8000
```

## API endpoints

- `GET /api/levels/<sym>?level_type=<type>` — MenthorQ precomputed levels.
- `GET /api/gex/<sym>` — gex-api raw chain + computed levels.

Both return JSON including a `tv_string` ready to paste into TradingView.

## Known limitations

- **GEX API mode does not apply the index↔futures basis.** Strikes are SPX/NDX values printed under `$ES1!`/`$NQ1!`. The cash-vs-futures basis (a handful of points for ES, ~100–200 for NQ) is not added. MenthorQ mode does not have this issue because the API returns futures-priced levels directly.
- **Delayed data.** The GEX API upstream is delayed ~15 min. MenthorQ's EOD types are end-of-day; the `*_intraday` types update during the session.
- **`1D Min/Max` is an approximation** — `spot ± 1σ` from the underlying's `iv30` (GEX API) or computed upstream (MenthorQ).

## Project layout

- `app.py` — Flask app. Two sources (MenthorQ + gex-api), OCC parsing, GEX/level computation.
- `templates/index.html` — single-page UI, vanilla JS + Plotly from CDN. Source toggle swaps controls and chart.
- `Dockerfile` — `python:3.12-slim` + gunicorn.
- `requirements.txt` — flask, requests, gunicorn.

## License

MIT — see [LICENSE](LICENSE).
