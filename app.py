import math
import re
from collections import defaultdict
from datetime import date

from flask import Flask, jsonify, render_template
import requests

UPSTREAM = "https://gex-api-xiq6cw7ltq-ue.a.run.app/delayed_options/{sym}"
OCC_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")
DEFAULT_SYM = "ES"

# User-facing symbol -> upstream symbol on the gex-api
ALIAS = {
    "ES": "_SPX",
    "NQ": "_NDX",
    "RTY": "_RUT",
    "VX":  "_VIX",
    "YM":  "_DJX",
}

# User-facing symbol -> TradingView ticker for the export string
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

app = Flask(__name__)


def parse_occ(sym):
    m = OCC_RE.match(sym)
    if not m:
        return None
    _, yymmdd, cp, strike8 = m.groups()
    expiry = f"20{yymmdd[0:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
    return expiry, cp, int(strike8) / 1000.0


def fetch(sym):
    r = requests.get(UPSTREAM.format(sym=sym), timeout=30)
    r.raise_for_status()
    return r.json()


def resolve(user_sym):
    """Return (upstream_sym, payload). Tries alias, then raw, then underscore fallback."""
    user_sym = user_sym.upper()
    upstream = ALIAS.get(user_sym, user_sym)
    payload = fetch(upstream)
    if payload.get("error") and not upstream.startswith("_"):
        alt = fetch("_" + upstream)
        if not alt.get("error"):
            return "_" + upstream, alt
    return upstream, payload


def compute_levels(rows, meta, user_sym):
    """SpotGamma-style levels from per-contract rows."""
    if not rows:
        return {}

    spot = meta.get("current_price")
    iv30 = (meta.get("iv30") or 0) / 100.0  # annualized
    sigma_1d = spot * iv30 * math.sqrt(1 / 365.0) if spot else 0

    # Aggregate net GEX by strike (across all expiries)
    by_strike = defaultdict(float)
    call_gamma_oi = defaultdict(float)
    put_gamma_oi = defaultdict(float)
    for r in rows:
        by_strike[r["strike"]] += r["gex"]
        if r["right"] == "C":
            call_gamma_oi[r["strike"]] += r["gamma"] * r["oi"]
        else:
            put_gamma_oi[r["strike"]] += r["gamma"] * r["oi"]

    strikes_sorted = sorted(by_strike.keys())
    net_by_strike = [(s, by_strike[s]) for s in strikes_sorted]

    # Call Resistance: largest positive net GEX strike
    call_res = max(net_by_strike, key=lambda x: x[1])[0]
    # Put Support: largest negative net GEX strike (most negative)
    put_sup = min(net_by_strike, key=lambda x: x[1])[0]

    # HVL / gamma flip: cumulative net GEX crosses zero
    hvl = None
    cum = 0
    prev = 0
    for s, g in net_by_strike:
        prev = cum
        cum += g
        if prev != 0 and (prev < 0) != (cum < 0):
            hvl = s
            break
    if hvl is None:
        hvl = call_res  # fallback

    # 0DTE: contracts expiring today
    today = date.today().isoformat()
    zero_dte = [r for r in rows if r["expiry"] == today]
    if not zero_dte:
        # fall back to nearest future expiry
        future = sorted({r["expiry"] for r in rows if r["expiry"] >= today})
        nearest = future[0] if future else None
        zero_dte = [r for r in rows if r["expiry"] == nearest] if nearest else []

    zdte_levels = {}
    if zero_dte:
        z_by_strike = defaultdict(float)
        z_call_g = defaultdict(float)
        z_put_g = defaultdict(float)
        for r in zero_dte:
            z_by_strike[r["strike"]] += r["gex"]
            if r["right"] == "C":
                z_call_g[r["strike"]] += r["gamma"] * r["oi"]
            else:
                z_put_g[r["strike"]] += r["gamma"] * r["oi"]
        z_pairs = sorted(z_by_strike.items())
        z_call_res = max(z_pairs, key=lambda x: x[1])[0]
        z_put_sup = min(z_pairs, key=lambda x: x[1])[0]
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
        # Gamma wall: largest absolute call gamma·OI
        gamma_wall = max(z_call_g.items(), key=lambda x: x[1])[0] if z_call_g else z_call_res
        zdte_levels = {
            "call_resistance_0dte": z_call_res,
            "put_support_0dte": z_put_sup,
            "hvl_0dte": z_hvl,
            "gamma_wall_0dte": gamma_wall,
        }

    # Top 10 strikes by |net GEX|
    top10 = sorted(net_by_strike, key=lambda x: abs(x[1]), reverse=True)[:10]
    gex_ranked = [s for s, _ in top10]

    levels = {
        "spot": spot,
        "call_resistance": call_res,
        "put_support": put_sup,
        "hvl": hvl,
        "one_day_min": round(spot - sigma_1d, 2) if spot else None,
        "one_day_max": round(spot + sigma_1d, 2) if spot else None,
        **zdte_levels,
        "gex_ranked": gex_ranked,
    }
    return levels


def fmt_strike(x):
    if x is None:
        return ""
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def build_tv_string(user_sym, levels):
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
    out = "".join(parts).rstrip(",")
    return out


@app.get("/api/gex/<sym>")
def api_gex(sym):
    user_sym = sym.upper()
    upstream, payload = resolve(user_sym)
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

    levels = compute_levels(rows, data, user_sym)
    return jsonify({
        "timestamp": payload.get("timestamp"),
        "user_symbol": user_sym,
        "upstream_symbol": upstream,
        "tv_ticker": TV_TICKER.get(user_sym, user_sym),
        "spot": data.get("current_price"),
        "iv30": data.get("iv30"),
        "rows": rows,
        "levels": levels,
        "tv_string": build_tv_string(user_sym, levels),
    })


@app.get("/")
@app.get("/<sym>")
def index(sym=DEFAULT_SYM):
    return render_template("index.html", sym=sym.upper())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
