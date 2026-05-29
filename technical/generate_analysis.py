"""
generate_analysis.py
Fetches OHLC from TwelveData, computes Ichimoku H4/H1/M15,
writes technical/analysis.json

Env var required: TWELVE_API_KEY
"""

import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone
from typing import Optional

API_KEY = os.environ.get("TWELVE_API_KEY", "")
if not API_KEY:
    print("ERROR: TWELVE_API_KEY not set", file=sys.stderr)
    sys.exit(1)

# TwelveData uses slash format for forex pairs
PAIRS = [
    "USD/JPY", "EUR/USD", "GBP/USD", "AUD/JPY", "USD/SGD",
    "AUD/USD", "AUD/SGD", "AUD/CAD", "EUR/AUD", "EUR/GBP",
    "EUR/SGD", "EUR/JPY", "GBP/JPY", "GBP/AUD", "XAG/USD", "XAU/USD",
]

# Display names (no slash)
def display(sym): return sym.replace("/", "")

CURRENCY_SIDES = {
    "USD": [("USD/JPY","base"),("USD/SGD","base"),("EUR/USD","quote"),("GBP/USD","quote"),
            ("AUD/USD","quote"),("XAG/USD","quote"),("XAU/USD","quote")],
    "EUR": [("EUR/USD","base"),("EUR/AUD","base"),("EUR/GBP","base"),("EUR/SGD","base"),("EUR/JPY","base")],
    "GBP": [("GBP/USD","base"),("GBP/JPY","base"),("GBP/AUD","base"),("EUR/GBP","quote")],
    "AUD": [("AUD/USD","base"),("AUD/JPY","base"),("AUD/SGD","base"),("AUD/CAD","base"),
            ("EUR/AUD","quote"),("GBP/AUD","quote")],
    "JPY": [("USD/JPY","quote"),("AUD/JPY","quote"),("EUR/JPY","quote"),("GBP/JPY","quote")],
    "SGD": [("USD/SGD","quote"),("AUD/SGD","quote"),("EUR/SGD","quote")],
    "CAD": [("AUD/CAD","quote")],
    "XAU": [("XAU/USD","base")],
}

TENKAN, KIJUN, SENKOU = 9, 26, 52


def fetch_ohlc(symbol: str, interval: str, outputsize: int = 100) -> Optional[list]:
    params = urllib.parse.urlencode({
        "symbol": symbol, "interval": interval,
        "outputsize": outputsize, "apikey": API_KEY,
    })
    url = f"https://api.twelvedata.com/time_series?{params}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.loads(r.read())
        if data.get("status") == "error":
            print(f"    API error [{symbol}/{interval}]: {data.get('message','')}")
            return None
        values = data.get("values", [])
        if not values:
            print(f"    Empty values [{symbol}/{interval}]")
            return None
        return list(reversed(values))  # oldest first
    except Exception as e:
        print(f"    Fetch error [{symbol}/{interval}]: {e}")
        return None


def midpoint(h_slice, l_slice):
    return (max(h_slice) + min(l_slice)) / 2


def ichimoku_signal(candles: list) -> str:
    if len(candles) < SENKOU:
        return "neutral"
    highs  = [float(c["high"])  for c in candles]
    lows   = [float(c["low"])   for c in candles]
    closes = [float(c["close"]) for c in candles]
    i = len(candles) - 1

    tenkan = midpoint(highs[i-TENKAN+1:i+1], lows[i-TENKAN+1:i+1])
    kijun  = midpoint(highs[i-KIJUN+1:i+1],  lows[i-KIJUN+1:i+1])

    sa_idx = max(TENKAN-1, i - KIJUN)
    sb_idx = max(SENKOU-1, i - KIJUN)
    sa = midpoint(highs[sa_idx-TENKAN+1:sa_idx+1], lows[sa_idx-TENKAN+1:sa_idx+1])
    sb = midpoint(highs[sb_idx-SENKOU+1:sb_idx+1], lows[sb_idx-SENKOU+1:sb_idx+1])

    price     = closes[i]
    cloud_top = max(sa, sb)
    cloud_bot = min(sa, sb)

    if price > cloud_top and tenkan >= kijun:
        return "bull"
    elif price < cloud_bot and tenkan <= kijun:
        return "bear"
    return "neutral"


def change_pct(candles: list) -> float:
    if len(candles) < 2: return 0.0
    prev = float(candles[-2]["close"])
    curr = float(candles[-1]["close"])
    return round((curr - prev) / prev * 100, 4) if prev else 0.0


def suggest_levels(candles_h1: list, signal: str) -> dict:
    if len(candles_h1) < 20 or signal == "neutral": return {}
    recent = candles_h1[-20:]
    highs  = [float(c["high"]) for c in recent]
    lows   = [float(c["low"])  for c in recent]
    price  = float(candles_h1[-1]["close"])
    dp     = 2 if price > 20 else 5
    if signal == "bear":
        return {"entry": f"{price:.{dp}f}", "sl": f"{max(highs)*1.001:.{dp}f}", "tp1": f"{min(lows)*0.999:.{dp}f}"}
    else:
        return {"entry": f"{price:.{dp}f}", "sl": f"{min(lows)*0.999:.{dp}f}", "tp1": f"{max(highs)*1.001:.{dp}f}"}


def note_for(h4, h1, m15):
    if h4 == h1 == m15 and h4 != "neutral":
        return f"All TFs aligned {h4} — strong signal"
    if h4 == h1 and h4 != "neutral":
        return f"H4 + H1 aligned {h4}; M15 mixed"
    if h4 == "neutral":
        return "H4 inside cloud — wait for breakout"
    return f"H4 {h4} but lower TFs diverging"


def currency_bias(pair_results):
    votes = {c: {"bull":0,"bear":0} for c in CURRENCY_SIDES}
    for p in pair_results:
        sig = p.get("h4_signal","neutral")
        if sig == "neutral": continue
        for ccy, pairs in CURRENCY_SIDES.items():
            for psym, side in pairs:
                if psym == p["_sym"]:
                    vote = sig if side == "base" else ("bear" if sig == "bull" else "bull")
                    votes[ccy][vote] += 1
    return {c: ("bull" if v["bull"]>v["bear"] else "bear" if v["bear"]>v["bull"] else "neutral")
            for c, v in votes.items()}


def main():
    print(f"Running analysis for {len(PAIRS)} pairs")
    results = []

    for sym in PAIRS:
        print(f"  {sym}...", end=" ", flush=True)

        c_h4  = fetch_ohlc(sym, "4h",   80)
        time.sleep(0.3)  # stay under rate limit
        c_h1  = fetch_ohlc(sym, "1h",  100)
        time.sleep(0.3)
        c_m15 = fetch_ohlc(sym, "15min", 100)
        time.sleep(0.3)

        if not c_h4 or not c_h1:
            print("SKIP")
            continue

        h4  = ichimoku_signal(c_h4)
        h1  = ichimoku_signal(c_h1)
        m15 = ichimoku_signal(c_m15) if c_m15 else "neutral"

        price = float(c_h1[-1]["close"])
        dp    = 2 if price > 20 else 5

        result = {
            "_sym":       sym,
            "pair":       display(sym),
            "price":      f"{price:.{dp}f}",
            "change_pct": change_pct(c_h1),
            "h4_signal":  h4,
            "h1_signal":  h1,
            "m15_signal": m15,
            "priority":   h4 != "neutral" and h4 == h1,
            "note":       note_for(h4, h1, m15),
            **suggest_levels(c_h1, h4),
        }
        results.append(result)
        print(f"H4={h4} H1={h1} M15={m15}")

    bias = currency_bias(results)

    # Strip internal _sym key before saving
    for r in results:
        r.pop("_sym", None)

    out = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "currency_bias": bias,
        "pairs":         results,
    }

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)

    print(f"\nWrote {len(results)} pairs → {path}")
    print(f"Priority setups: {sum(1 for r in results if r['priority'])}")
    print(f"Currency bias: {bias}")


if __name__ == "__main__":
    main()
