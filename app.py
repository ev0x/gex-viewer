import math
import os
import re
from collections import defaultdict
from datetime import date

from flask import Flask, jsonify, render_template, request
import requests

# ---- MenthorQ (precomputed levels) ----------------------------------------
MENTHORQ_URL = "https://api.menthorq.io/getDailyLevels"
MENTHORQ_KEY = os.environ.get("MENTHORQ_API_KEY", "")
MENTHORQ_USER_ID = os.environ.get("MENTHORQ_USER_ID", "gex-viewer")
LEVEL_TYPES = [
    "gamma_levels",
    "gamma_levels_intraday",
    "gamma_scalping",
    "gamma_scalping_intraday",
    "blindspots",
    "swing_levels",
]
DEFAULT_LEVEL_TYPE = "gamma_levels"

# ---- gex-api (raw option chain) -------------------------------------------
GEX_UPSTREAM = "https://gex-api-xiq6cw7ltq-ue.a.run.app/delayed_options/{sym}"
OCC_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")
ALIAS = {
    "ES":  "_SPX",
    "NQ":  "_NDX",
    "RTY": "_RUT",
    "VX":  "_VIX",
    "YM":  "_DJX",
}
TV_TICKER = {
    "ES":  "ES1!",
    "NQ":  "NQ1!",
    "RTY": "RTY1!",
    "VX":  "VX1!",
    "YM":  "YM1!",
    "SPX": "SPX",
    "NDX": "NDX",
    "RUT": "RUT",
    "VIX": "VIX",
    "DJX": "DJX",
    "XSP": "XSP",
}

DEFAULT_SYM = "ES"
DEFAULT_SOURCE = "menthorq"

app = Flask(__name__)


# =========================================================================
# Shared helpers
# =========================================================================

def fmt_strike(x):
    if x is None:
        return ""
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


# =========================================================================
# MenthorQ source
# =========================================================================

def fetch_menthorq(ticker, level_type):
    r = requests.get(
        MENTHORQ_URL,
        params={
            "platform": "sc",
            "ticker": ticker,
            "level_type": level_type,
            "user_id": MENTHORQ_USER_ID,
        },
        headers={"X-API-KEY": MENTHORQ_KEY},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def build_tv_string_menthorq(tv_ticker, level_values):
    if not level_values:
        return ""
    parts = [f"${tv_ticker}:"]
    for lv in level_values:
        val = lv.get("value")
        if val is None:
            continue
        parts.append(f" {lv.get('name')}, {fmt_strike(val)},")
    return "".join(parts).rstrip(",")


@app.get("/api/levels/<sym>")
def api_levels(sym):
    if not MENTHORQ_KEY:
        return jsonify({
            "error": True,
            "message": "MENTHORQ_API_KEY env var is not set on the server",
        }), 500

    level_type = request.args.get("level_type", DEFAULT_LEVEL_TYPE)
    if level_type not in LEVEL_TYPES:
        return jsonify({"error": True, "message": f"unknown level_type {level_type}"}), 400

    try:
        payload = fetch_menthorq(sym.upper(), level_type)
    except requests.RequestException as e:
        return jsonify({"error": True, "message": str(e)}), 502

    ticker_mq = payload.get("ticker_mq") or sym.upper()
    levels = payload.get("levels") or []
    if not levels:
        return jsonify({
            "error": True,
            "message": "no levels returned for this ticker/level_type",
            "ticker": payload.get("ticker"),
            "ticker_mq": ticker_mq,
            "not_existing_levels": payload.get("not_existing_levels", []),
        }), 502

    block = levels[0]
    level_values = block.get("level_values") or []

    # Implied spot ≈ midpoint of 1D Min/Max when present (gamma_* level types)
    by_name = {lv["name"]: lv["value"] for lv in level_values if lv.get("value") is not None}
    spot = None
    if "1D Min" in by_name and "1D Max" in by_name:
        spot = round((by_name["1D Min"] + by_name["1D Max"]) / 2, 2)

    return jsonify({
        "ticker": payload.get("ticker"),
        "ticker_mq": ticker_mq,
        "level_type": block.get("level_type"),
        "kind": block.get("kind"),
        "date": block.get("date"),
        "level_values": level_values,
        "spot": spot,
        "tv_string": build_tv_string_menthorq(ticker_mq, level_values),
    })


# =========================================================================
# gex-api source (raw option chain → computed levels + histogram data)
# =========================================================================

def parse_occ(sym):
    m = OCC_RE.match(sym)
    if not m:
        return None
    _, yymmdd, cp, strike8 = m.groups()
    expiry = f"20{yymmdd[0:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
    return expiry, cp, int(strike8) / 1000.0


def fetch_gex(sym):
    r = requests.get(GEX_UPSTREAM.format(sym=sym), timeout=30)
    r.raise_for_status()
    return r.json()


def resolve_gex(user_sym):
    """Return (upstream_sym, payload). Tries alias, then raw, then underscore fallback."""
    user_sym = user_sym.upper()
    upstream = ALIAS.get(user_sym, user_sym)
    payload = fetch_gex(upstream)
    if payload.get("error") and not upstream.startswith("_"):
        alt = fetch_gex("_" + upstream)
        if not alt.get("error"):
            return "_" + upstream, alt
    return upstream, payload


def compute_levels(rows, meta):
    if not rows:
        return {}

    spot = meta.get("current_price")
    iv30 = (meta.get("iv30") or 0) / 100.0
    sigma_1d = spot * iv30 * math.sqrt(1 / 365.0) if spot else 0

    by_strike = defaultdict(float)
    for r in rows:
        by_strike[r["strike"]] += r["gex"]

    strikes_sorted = sorted(by_strike.keys())
    net_by_strike = [(s, by_strike[s]) for s in strikes_sorted]

    call_res = max(net_by_strike, key=lambda x: x[1])[0]
    put_sup  = min(net_by_strike, key=lambda x: x[1])[0]

    hvl = None
    cum = 0
    for s, g in net_by_strike:
        prev = cum
        cum += g
        if prev != 0 and (prev < 0) != (cum < 0):
            hvl = s
            break
    if hvl is None:
        hvl = call_res

    today = date.today().isoformat()
    zero_dte = [r for r in rows if r["expiry"] == today]
    if not zero_dte:
        future = sorted({r["expiry"] for r in rows if r["expiry"] >= today})
        nearest = future[0] if future else None
        zero_dte = [r for r in rows if r["expiry"] == nearest] if nearest else []

    zdte_levels = {}
    if zero_dte:
        z_by_strike = defaultdict(float)
        z_call_g = defaultdict(float)
        for r in zero_dte:
            z_by_strike[r["strike"]] += r["gex"]
            if r["right"] == "C":
                z_call_g[r["strike"]] += r["gamma"] * r["oi"]
        z_pairs = sorted(z_by_strike.items())
        z_call_res = max(z_pairs, key=lambda x: x[1])[0]
        z_put_sup  = min(z_pairs, key=lambda x: x[1])[0]
        z_hvl = None
        cum = 0
        for s, g in z_pairs:
            prev = cum
            cum += g
            if prev != 0 and (prev < 0) != (cum < 0):
                z_hvl = s
                break
        if z_hvl is None:
            z_hvl = z_call_res
        gamma_wall = max(z_call_g.items(), key=lambda x: x[1])[0] if z_call_g else z_call_res
        zdte_levels = {
            "call_resistance_0dte": z_call_res,
            "put_support_0dte": z_put_sup,
            "hvl_0dte": z_hvl,
            "gamma_wall_0dte": gamma_wall,
        }

    top10 = sorted(net_by_strike, key=lambda x: abs(x[1]), reverse=True)[:10]
    gex_ranked = [s for s, _ in top10]

    return {
        "spot": spot,
        "call_resistance": call_res,
        "put_support": put_sup,
        "hvl": hvl,
        "one_day_min": round(spot - sigma_1d, 2) if spot else None,
        "one_day_max": round(spot + sigma_1d, 2) if spot else None,
        **zdte_levels,
        "gex_ranked": gex_ranked,
    }


def build_tv_string_gex(user_sym, levels):
    if not levels:
        return ""
    tv = TV_TICKER.get(user_sym, user_sym)
    parts = [f"${tv}:"]

    def add(label, val):
        if val is None:
            return
        parts.append(f" {label}, {fmt_strike(val)},")

    add("Call Resistance", levels.get("call_resistance"))
    add("Put Support",     levels.get("put_support"))
    add("HVL",             levels.get("hvl"))
    add("1D Min",          levels.get("one_day_min"))
    add("1D Max",          levels.get("one_day_max"))
    add("Call Resistance 0DTE", levels.get("call_resistance_0dte"))
    add("Put Support 0DTE",     levels.get("put_support_0dte"))
    add("HVL 0DTE",             levels.get("hvl_0dte"))
    add("Gamma Wall 0DTE",      levels.get("gamma_wall_0dte"))
    for i, s in enumerate(levels.get("gex_ranked", []), 1):
        add(f"GEX {i}", s)
    return "".join(parts).rstrip(",")


@app.get("/api/gex/<sym>")
def api_gex(sym):
    user_sym = sym.upper()
    try:
        upstream, payload = resolve_gex(user_sym)
    except requests.RequestException as e:
        return jsonify({"error": True, "message": str(e)}), 502
    if payload.get("error"):
        return jsonify({"error": True, "symbol": user_sym, "rows": []}), 502

    data = payload.get("data", {}) or {}
    options = data.get("options", []) or []

    rows = []
    for o in options:
        parsed = parse_occ(o.get("option", ""))
        if not parsed:
            continue
        expiry, cp, strike = parsed
        gamma = o.get("gamma") or 0.0
        oi = o.get("open_interest") or 0
        sign = 1 if cp == "C" else -1
        rows.append({
            "expiry": expiry,
            "right": cp,
            "strike": strike,
            "gamma": gamma,
            "oi": oi,
            "gex": sign * gamma * oi * 100,
        })

    levels = compute_levels(rows, data)
    return jsonify({
        "timestamp": payload.get("timestamp"),
        "user_symbol": user_sym,
        "upstream_symbol": upstream,
        "tv_ticker": TV_TICKER.get(user_sym, user_sym),
        "spot": data.get("current_price"),
        "iv30": data.get("iv30"),
        "rows": rows,
        "levels": levels,
        "tv_string": build_tv_string_gex(user_sym, levels),
    })


# =========================================================================
# Page
# =========================================================================

@app.get("/")
@app.get("/<sym>")
def index(sym=DEFAULT_SYM):
    source = request.args.get("source", DEFAULT_SOURCE)
    if source not in ("menthorq", "gexapi"):
        source = DEFAULT_SOURCE
    return render_template(
        "index.html",
        sym=sym.upper(),
        level_types=LEVEL_TYPES,
        default_level_type=DEFAULT_LEVEL_TYPE,
        default_source=source,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
