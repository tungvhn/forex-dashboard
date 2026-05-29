"""
generate_analysis.py
Fetches OHLC data from TwelveData, computes Ichimoku signals across H4/H1/M15,
and writes technical/analysis.json for the dashboard to read.

Run via GitHub Actions (see .github/workflows/update-analysis.yml)
Requires env var: TWELVE_API_KEY
"""

import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional
import urllib.request
import urllib.parse

API_KEY = os.environ.get("TWELVE_API_KEY", "")
if not API_KEY:
    print("ERROR: TWELVE_API_KEY not set", file=sys.stderr)
    sys.exit(1)

# ── Watchlist ──────────────────────────────────────────────────────────────────
PAIRS = [
    "USDJPY", "EURUSD", "GBPUSD", "AUDJPY", "USDSGD",
    "AUDUSD", "AUDSGD", "AUDCAD", "EURAUD", "EURGBP",
    "EURSGD", "EURJPY", "GBPJPY", "GBPAUD", "XAGUSD", "XAUUSD",
]

# Currency → which side of the pair it sits on (base or quote)
CURRENCY_SIDES = {
    "USD": [("USDJPY","base"),("USDSGD","base"),("EURUSD","quote"),("GBPUSD","quote"),
            ("AUDUSD","quote"),("XAGUSD","quote"),("XAUUSD","quote")],
    "EUR": [("EURUSD","base"),("EURAUD","base"),("EURGBP","base"),("EURSGD","base"),("EURJPY","base")],
    "GBP": [("GBPUSD","base"),("GBPJPY","base"),("GBPAUD","base"),("EURGBP","quote")],
    "AUD": [("AUDUSD","base"),("AUDJPY","base"),("AUDSGD","base"),("AUDCAD","base"),
            ("EURAUD","quote"),("GBPAUD","quote")],
    "JPY": [("USDJPY","quote"),("AUDJPY","quote"),("EURJPY","quote"),("GBPJPY","quote")],
    "SGD": [("USDSGD","quote"),("AUDSGD","quote"),("EURSGD","quote")],
    "CAD": [("AUDCAD","quote")],
    "XAU": [("XAUUSD","base")],
}

# Ichimoku parameters
TENKAN = 9
KIJUN  = 26
SENKOU = 52


def fetch_ohlc(symbol: str, interval: str, outputsize: int = 100) -> Optional[list]:
    """Fetch OHLC candles from TwelveData. Returns list of dicts or None on error."""
    params = urllib.parse.urlencode({
        "symbol":     symbol,
        "interval":   interval,
        "outputsize": outputsize,
        "apikey":     API_KEY,
    })
    url = f"https://api.twelvedata.com/time_series?{params}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
        if data.get("status") == "error":
            print(f"  TwelveData error for {symbol}/{interval}: {data.get('message')}")
            return None
        values = data.get("values", [])
        # TwelveData returns newest-first; reverse to oldest-first
        return list(reversed(values))
    except Exception as e:
        print(f"  Fetch error {symbol}/{interval}: {e}")
        return None


def ichimoku_signal(candles: list) -> str:
    """
    Compute Ichimoku cloud bias from a list of OHLC dicts (oldest-first).
    Returns 'bull', 'bear', or 'neutral'.
    """
    if len(candles) < SENKOU:
        return "neutral"

    highs  = [float(c["high"])  for c in candles]
    lows   = [float(c["low"])   for c in candles]
    closes = [float(c["close"]) for c in candles]

    def midpoint(h_slice, l_slice):
        return (max(h_slice) + min(l_slice)) / 2

    n = len(candles)
    # Use last complete candle index
    i = n - 1

    tenkan = midpoint(highs[i-TENKAN+1:i+1], lows[i-TENKAN+1:i+1])
    kijun  = midpoint(highs[i-KIJUN+1:i+1],  lows[i-KIJUN+1:i+1])

    # Senkou spans are projected 26 periods forward, so for current price
    # we look at senkou computed 26 periods ago
    sa_idx = max(0, i - KIJUN)
    sb_idx = max(0, i - KIJUN)

    sa = midpoint(highs[sa_idx-TENKAN+1:sa_idx+1], lows[sa_idx-TENKAN+1:sa_idx+1]) if sa_idx >= TENKAN else None
    if sb_idx >= SENKOU:
        sb = midpoint(highs[sb_idx-SENKOU+1:sb_idx+1], lows[sb_idx-SENKOU+1:sb_idx+1])
    else:
        sb = None

    price = closes[i]

    # Price vs cloud
    if sa is not None and sb is not None:
        cloud_top = max(sa, sb)
        cloud_bot = min(sa, sb)
        if price > cloud_top and tenkan >= kijun:
            return "bull"
        elif price < cloud_bot and tenkan <= kijun:
            return "bear"
    return "neutral"


def compute_change_pct(candles: list) -> float:
    if len(candles) < 2:
        return 0.0
    prev  = float(candles[-2]["close"])
    curr  = float(candles[-1]["close"])
    if prev == 0:
        return 0.0
    return round((curr - prev) / prev * 100, 4)


def suggest_levels(pair: str, candles_h1: list, signal: str) -> dict:
    """
    Very simple level suggestion based on recent high/low.
    Returns entry, sl, tp1 as strings (5 sig figs).
    """
    if len(candles_h1) < 20:
        return {}
    recent = candles_h1[-20:]
    highs  = [float(c["high"])  for c in recent]
    lows   = [float(c["low"])   for c in recent]
    price  = float(candles_h1[-1]["close"])

    # Determine decimal places from price magnitude
    dp = 2 if price > 20 else 5

    if signal == "bear":
        entry = round(price, dp)
        sl    = round(max(highs) * 1.001, dp)
        tp1   = round(min(lows) * 0.999, dp)
    elif signal == "bull":
        entry = round(price, dp)
        sl    = round(min(lows) * 0.999, dp)
        tp1   = round(max(highs) * 1.001, dp)
    else:
        return {}

    return {
        "entry": f"{entry:.{dp}f}",
        "sl":    f"{sl:.{dp}f}",
        "tp1":   f"{tp1:.{dp}f}",
    }


def note_for(pair: str, h4: str, h1: str, m15: str) -> str:
    """Generate a short plain-English note."""
    aligned = h4 == h1 == m15
    if aligned and h4 == "bear":
        return "All TFs aligned bearish — strong sell bias"
    if aligned and h4 == "bull":
        return "All TFs aligned bullish — strong buy bias"
    if h4 != "neutral" and h1 == h4:
        return f"H4 + H1 aligned {h4}; M15 diverging"
    if h4 == "neutral":
        return "H4 in cloud — wait for breakout"
    return f"H4 {h4} but lower TFs mixed"


def compute_currency_bias(pair_results: list) -> dict:
    """
    Aggregate H4 signals per currency.
    Simple vote: count bull/bear appearances.
    """
    votes = {c: {"bull": 0, "bear": 0} for c in CURRENCY_SIDES}

    for p in pair_results:
        sym  = p["pair"]
        sig4 = p.get("h4_signal", "neutral")
        if sig4 == "neutral":
            continue
        for ccy, pairs in CURRENCY_SIDES.items():
            for psym, side in pairs:
                if psym == sym:
                    # If currency is on the base side, bull pair = bull currency
                    # If on quote side, bull pair = bear currency
                    if side == "base":
                        votes[ccy][sig4] += 1
                    else:
                        opp = "bear" if sig4 == "bull" else "bull"
                        votes[ccy][opp] += 1

    bias = {}
    for ccy, v in votes.items():
        if v["bull"] > v["bear"]:
            bias[ccy] = "bull"
        elif v["bear"] > v["bull"]:
            bias[ccy] = "bear"
        else:
            bias[ccy] = "neutral"
    return bias


def main():
    print(f"Starting analysis — {len(PAIRS)} pairs")
    pair_results = []

    for sym in PAIRS:
        print(f"  {sym}...", end=" ", flush=True)

        candles_h4  = fetch_ohlc(sym, "4h",  outputsize=80)
        candles_h1  = fetch_ohlc(sym, "1h",  outputsize=100)
        candles_m15 = fetch_ohlc(sym, "15min", outputsize=100)

        if not candles_h4 or not candles_h1:
            print("skipped (no data)")
            continue

        h4_sig  = ichimoku_signal(candles_h4)
        h1_sig  = ichimoku_signal(candles_h1)
        m15_sig = ichimoku_signal(candles_m15) if candles_m15 else "neutral"

        price_now = float(candles_h1[-1]["close"]) if candles_h1 else 0
        dp        = 2 if price_now > 20 else 5
        price_str = f"{price_now:.{dp}f}"

        chg_pct = compute_change_pct(candles_h1)
        levels  = suggest_levels(sym, candles_h1, h4_sig)
        note    = note_for(sym, h4_sig, h1_sig, m15_sig)

        # Mark as priority if H4 + H1 agree and signal is not neutral
        priority = (h4_sig != "neutral") and (h4_sig == h1_sig)

        result = {
            "pair":       sym,
            "price":      price_str,
            "change_pct": chg_pct,
            "h4_signal":  h4_sig,
            "h1_signal":  h1_sig,
            "m15_signal": m15_sig,
            "priority":   priority,
            "note":       note,
            **levels,
        }
        pair_results.append(result)
        print(f"H4={h4_sig} H1={h1_sig} M15={m15_sig}")

    currency_bias = compute_currency_bias(pair_results)

    output = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "currency_bias": currency_bias,
        "pairs":         pair_results,
    }

    out_path = os.path.join(os.path.dirname(__file__), "analysis.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nDone — {len(pair_results)} pairs written to {out_path}")
    priority_count = sum(1 for p in pair_results if p["priority"])
    print(f"Priority setups: {priority_count}")
    print(f"Currency bias: {currency_bias}")


if __name__ == "__main__":
    main()
