"""
generate_analysis.py
- Fetches price, pivot points, MA20/50/200, RSI from TwelveData
- Generates Vietnamese notes from smart templates (no external AI needed)
- Writes technical/analysis.json

Env var required: TWELVE_API_KEY
"""

import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone

TWELVE_KEY = os.environ.get("TWELVE_API_KEY", "")
if not TWELVE_KEY:
    print("ERROR: TWELVE_API_KEY not set", file=sys.stderr); sys.exit(1)

PAIRS = [
    "USD/JPY", "EUR/USD", "GBP/USD", "AUD/JPY", "USD/SGD",
    "AUD/USD", "AUD/SGD", "AUD/CAD", "EUR/AUD", "EUR/GBP",
    "EUR/SGD", "EUR/JPY", "GBP/JPY", "GBP/AUD", "XAG/USD", "XAU/USD",
]

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

def display(sym): return sym.replace("/","")

def td_get(path, params):
    qs = urllib.parse.urlencode({**params, "apikey": TWELVE_KEY})
    url = f"https://api.twelvedata.com/{path}?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.loads(r.read())
        if data.get("status") == "error":
            print(f"    TD error [{path}]: {data.get('message','')}")
            return None
        return data
    except Exception as e:
        print(f"    TD fetch error [{path}]: {e}")
        return None

def fetch_price(sym):
    d = td_get("price", {"symbol": sym})
    return float(d["price"]) if d and "price" in d else None

def fetch_change_pct(sym):
    d = td_get("quote", {"symbol": sym})
    if not d: return 0.0
    try: return round(float(d.get("percent_change", 0)), 4)
    except: return 0.0

def fetch_pivots(sym):
    d = td_get("pivot_points", {"symbol": sym, "interval": "1day", "outputsize": 1})
    if not d or "values" not in d or not d["values"]: return None
    v = d["values"][0]
    try:
        return {k: float(v[k]) for k in ["s2","s1","pp","r1","r2"]}
    except: return None

def fetch_ma(sym, period, interval="4h"):
    d = td_get("ma", {"symbol": sym, "interval": interval, "time_period": period, "outputsize": 1})
    if not d or "values" not in d or not d["values"]: return None
    try: return float(d["values"][0]["ma"])
    except: return None

def fetch_rsi(sym, interval="4h"):
    d = td_get("rsi", {"symbol": sym, "interval": interval, "time_period": 14, "outputsize": 1})
    if not d or "values" not in d or not d["values"]: return None
    try: return round(float(d["values"][0]["rsi"]), 1)
    except: return None

def ma_bias(price, ma20, ma50, ma200):
    above = [ma20 and price > ma20, ma50 and price > ma50, ma200 and price > ma200]
    cnt = sum(1 for b in above if b)
    if cnt == 3: return "bull", above
    if cnt == 0: return "bear", above
    if cnt == 2: return "partial", above
    return "neutral", above

def overall_bias(price, pivots, ma_sig, rsi):
    score = 0
    if pivots:
        score += 1 if price > pivots["pp"] else -1
    if ma_sig == "bull": score += 2
    elif ma_sig == "bear": score -= 2
    elif ma_sig == "partial": score += 1
    if rsi:
        if rsi > 55: score += 1
        elif rsi < 45: score -= 1
    if score >= 2: return "bull"
    if score <= -2: return "bear"
    return "neutral"

def generate_note(price_f, pivots, ma_bull, rsi, bias, chg):
    parts = []
    if pivots:
        pp, r1, r2, s1, s2 = pivots["pp"], pivots["r1"], pivots["r2"], pivots["s1"], pivots["s2"]
        if price_f >= r2:
            parts.append("Vượt R2")
        elif price_f >= r1:
            parts.append("Giữa R1–R2")
        elif price_f >= pp:
            ratio = (price_f - pp) / (r1 - pp) if r1 != pp else 0
            parts.append("Sát R1" if ratio > 0.75 else "Trên PP")
        elif price_f >= s1:
            ratio = (pp - price_f) / (pp - s1) if pp != s1 else 0
            parts.append("Sát PP từ dưới" if ratio < 0.25 else "Dưới PP")
        elif price_f >= s2:
            parts.append("Giữa S1–S2")
        else:
            parts.append("Phá S2")

    above = sum(1 for b in ma_bull if b)
    if above == 3: parts.append("tất cả MA hỗ trợ")
    elif above == 2: parts.append("2/3 MA hỗ trợ")
    elif above == 1: parts.append("1/3 MA hỗ trợ")
    else: parts.append("dưới tất cả MA")

    if rsi:
        if rsi >= 70: parts.append("RSI overbought")
        elif rsi <= 30: parts.append("RSI oversold")
        elif rsi >= 60: parts.append("RSI tích cực")
        elif rsi <= 40: parts.append("RSI yếu")

    if abs(chg) >= 0.3:
        parts.append("đang " + ("tăng mạnh" if chg > 0 else "giảm mạnh"))

    if bias == "bull": parts.append("→ bias tăng")
    elif bias == "bear": parts.append("→ bias giảm")
    else: parts.append("→ chờ tín hiệu")

    return ", ".join(parts)

def currency_bias(pair_results):
    votes = {c: {"bull":0,"bear":0} for c in CURRENCY_SIDES}
    for p in pair_results:
        sig = p.get("bias","neutral")
        if sig == "neutral": continue
        for ccy, pairs in CURRENCY_SIDES.items():
            for psym, side in pairs:
                if psym == p["_sym"]:
                    vote = sig if side == "base" else ("bear" if sig == "bull" else "bull")
                    votes[ccy][vote] += 1
    return {c: ("bull" if v["bull"]>v["bear"] else "bear" if v["bear"]>v["bull"] else "neutral")
            for c, v in votes.items()}

def fmt(price):
    return f"{price:.2f}" if price > 20 else f"{price:.5f}"

def main():
    print(f"Fetching data for {len(PAIRS)} pairs...")
    results = []

    for sym in PAIRS:
        dname = display(sym)
        print(f"  {dname}...", end=" ", flush=True)

        price  = fetch_price(sym);       time.sleep(1)
        chg    = fetch_change_pct(sym);  time.sleep(1)
        pivots = fetch_pivots(sym);      time.sleep(1)
        ma20   = fetch_ma(sym, 20);      time.sleep(1)
        ma50   = fetch_ma(sym, 50);      time.sleep(1)
        ma200  = fetch_ma(sym, 200);     time.sleep(1)
        rsi    = fetch_rsi(sym);         time.sleep(1)

        if price is None:
            print("SKIP"); continue

        ma_sig, ma_bull = ma_bias(price, ma20, ma50, ma200)
        bias     = overall_bias(price, pivots, ma_sig, rsi)
        priority = bias != "neutral" and ma_sig in ("bull","bear") and bias == ma_sig
        note     = generate_note(price, pivots, ma_bull, rsi, bias, chg)

        r = {
            "_sym":       sym,
            "pair":       dname,
            "price":      fmt(price),
            "change_pct": chg,
            "bias":       bias,
            "priority":   priority,
            "rsi":        rsi,
            "ma_label":   "Bullish" if ma_sig=="bull" else "Bearish" if ma_sig=="bear" else "Mixed",
            "ma_bull":    ma_bull,
            "pivots":     {k: fmt(v) for k,v in pivots.items()} if pivots else None,
            "note":       note,
        }

        if pivots:
            if bias == "bear":
                r["entry"] = fmt(price)
                r["sl"]    = fmt(pivots["r1"])
                r["tp1"]   = fmt(pivots["s1"])
            elif bias == "bull":
                r["entry"] = fmt(price)
                r["sl"]    = fmt(pivots["s1"])
                r["tp1"]   = fmt(pivots["r1"])

        results.append(r)
        print(f"bias={bias} RSI={rsi} MA={ma_sig}")

    cbias = currency_bias([{**r, "_sym": next(
        (s for s in PAIRS if display(s)==r["pair"]), "")} for r in results])

    for r in results:
        r.pop("_sym", None)

    output = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "currency_bias": cbias,
        "pairs":         results,
    }

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nDone — {len(results)} pairs → {path}")
    print(f"Priority setups: {sum(1 for r in results if r.get('priority'))}")
    print(f"Currency bias: {cbias}")

if __name__ == "__main__":
    main()
