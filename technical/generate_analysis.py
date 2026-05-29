"""
generate_analysis.py
TwelveData Basic 8: max 8 credits/min, 800/day
Pivot Points tự tính từ OHLC daily (Basic 8 không có pivot_points endpoint)
Formula: PP=(H+L+C)/3, R1=2PP-L, R2=PP+(H-L), S1=2PP-H, S2=PP-(H-L)
"""

import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone

TWELVE_KEY = os.environ.get("TWELVE_API_KEY", "")
if not TWELVE_KEY:
    print("ERROR: TWELVE_API_KEY not set", file=sys.stderr); sys.exit(1)

PAIRS = [
    "USD/JPY","EUR/USD","GBP/USD","AUD/JPY","USD/SGD",
    "AUD/USD","AUD/SGD","AUD/CAD","EUR/AUD","EUR/GBP",
    "EUR/SGD","EUR/JPY","GBP/JPY","GBP/AUD","XAG/USD","XAU/USD",
]
GROUP_A = PAIRS[:8]
GROUP_B = PAIRS[8:]

CURRENCY_SIDES = {
    "USD":[("USD/JPY","base"),("USD/SGD","base"),("EUR/USD","quote"),("GBP/USD","quote"),
           ("AUD/USD","quote"),("XAG/USD","quote"),("XAU/USD","quote")],
    "EUR":[("EUR/USD","base"),("EUR/AUD","base"),("EUR/GBP","base"),("EUR/SGD","base"),("EUR/JPY","base")],
    "GBP":[("GBP/USD","base"),("GBP/JPY","base"),("GBP/AUD","base"),("EUR/GBP","quote")],
    "AUD":[("AUD/USD","base"),("AUD/JPY","base"),("AUD/SGD","base"),("AUD/CAD","base"),
           ("EUR/AUD","quote"),("GBP/AUD","quote")],
    "JPY":[("USD/JPY","quote"),("AUD/JPY","quote"),("EUR/JPY","quote"),("GBP/JPY","quote")],
    "SGD":[("USD/SGD","quote"),("AUD/SGD","quote"),("EUR/SGD","quote")],
    "CAD":[("AUD/CAD","quote")],
    "XAU":[("XAU/USD","base")],
}

def display(sym): return sym.replace("/","")

def td_fetch(path, params, symbols):
    qs = urllib.parse.urlencode({**params, "symbol": ",".join(symbols), "apikey": TWELVE_KEY})
    url = f"https://api.twelvedata.com/{path}?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read())
        if isinstance(data, dict) and data.get("status") == "error":
            print(f"    TD error [{path}]: {data.get('message','')}")
            return {}
        if len(symbols) == 1:
            return {symbols[0]: data}
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"    fetch error [{path}]: {e}")
        return {}

def fetch_group(group, label):
    print(f"\n  === Group {label}: {[display(s) for s in group]} ===")
    results = {}

    # 7 calls, each separated by 65s to stay under 8 credits/min
    indicators = [
        ("price",       {},                                                  "price"),
        ("quote",       {},                                                  "quote"),
        ("time_series", {"interval":"1day","outputsize":2},                 "ohlc"),
        ("ma",          {"interval":"4h","time_period":20,"outputsize":1},  "ma20"),
        ("ma",          {"interval":"4h","time_period":50,"outputsize":1},  "ma50"),
        ("ma",          {"interval":"4h","time_period":200,"outputsize":1}, "ma200"),
        ("rsi",         {"interval":"4h","time_period":14,"outputsize":1},  "rsi"),
    ]

    for i, (path, params, key) in enumerate(indicators):
        print(f"    [{i+1}/7] {key}...", end=" ", flush=True)
        raw = td_fetch(path, params, group)
        for sym in group:
            if sym not in results:
                results[sym] = {}
            results[sym][key] = raw.get(sym, {})
        ok = sum(1 for sym in group if raw.get(sym))
        print(f"{ok}/{len(group)} ok")
        if i < len(indicators) - 1:
            time.sleep(65)

    return results

def calc_pivots(ohlc_raw):
    """Calculate classic pivot points from yesterday's OHLC."""
    try:
        values = ohlc_raw.get("values", [])
        # values[0] = today (incomplete), values[1] = yesterday (complete)
        prev = values[1] if len(values) >= 2 else values[0]
        h = float(prev["high"])
        l = float(prev["low"])
        c = float(prev["close"])
        pp = (h + l + c) / 3
        r1 = 2*pp - l
        r2 = pp + (h - l)
        s1 = 2*pp - h
        s2 = pp - (h - l)
        return {"pp":pp, "r1":r1, "r2":r2, "s1":s1, "s2":s2}
    except:
        return None

def parse_price(d):
    try: return float(d.get("price", 0) or 0) or None
    except: return None

def parse_change(d):
    try: return round(float(d.get("percent_change", 0) or 0), 4)
    except: return 0.0

def parse_ma(d):
    try:
        v = d.get("values", [d])[0] if "values" in d else d
        val = v.get("ma") or v.get("value")
        return float(val) if val else None
    except: return None

def parse_rsi(d):
    try:
        v = d.get("values", [d])[0] if "values" in d else d
        val = v.get("rsi") or v.get("value")
        return round(float(val), 1) if val else None
    except: return None

def ma_bias(price, ma20, ma50, ma200):
    above = [bool(ma20 and price > ma20),
             bool(ma50 and price > ma50),
             bool(ma200 and price > ma200)]
    cnt = sum(above)
    sig = "bull" if cnt==3 else "bear" if cnt==0 else "partial" if cnt==2 else "neutral"
    return sig, above

def overall_bias(price, pivots, ma_sig, rsi):
    score = 0
    if pivots: score += 1 if price > pivots["pp"] else -1
    score += {"bull":2,"bear":-2,"partial":1}.get(ma_sig, 0)
    if rsi:
        if rsi > 55: score += 1
        elif rsi < 45: score -= 1
    if score >= 2: return "bull"
    if score <= -2: return "bear"
    return "neutral"

def generate_note(price_f, pivots, ma_bull, rsi, bias, chg):
    parts = []
    if pivots:
        pp,r1,r2,s1,s2 = pivots["pp"],pivots["r1"],pivots["r2"],pivots["s1"],pivots["s2"]
        if price_f >= r2:   parts.append("Vượt R2")
        elif price_f >= r1: parts.append("Giữa R1–R2")
        elif price_f >= pp:
            ratio = (price_f-pp)/(r1-pp) if r1!=pp else 0
            parts.append("Sát R1" if ratio>0.75 else "Trên PP")
        elif price_f >= s1:
            ratio = (pp-price_f)/(pp-s1) if pp!=s1 else 0
            parts.append("Sát PP từ dưới" if ratio<0.25 else "Dưới PP")
        elif price_f >= s2: parts.append("Giữa S1–S2")
        else:               parts.append("Phá S2")

    above = sum(1 for b in ma_bull if b)
    if above==3:   parts.append("tất cả MA hỗ trợ")
    elif above==2: parts.append("2/3 MA hỗ trợ")
    elif above==1: parts.append("1/3 MA hỗ trợ")
    else:          parts.append("dưới tất cả MA")

    if rsi:
        if rsi>=70:   parts.append("RSI overbought")
        elif rsi<=30: parts.append("RSI oversold")
        elif rsi>=60: parts.append("RSI tích cực")
        elif rsi<=40: parts.append("RSI yếu")

    if abs(chg)>=0.3:
        parts.append("đang " + ("tăng mạnh" if chg>0 else "giảm mạnh"))

    parts.append("→ bias tăng" if bias=="bull" else
                 "→ bias giảm" if bias=="bear" else "→ chờ tín hiệu")
    return ", ".join(parts)

def currency_bias(pair_results):
    votes = {c:{"bull":0,"bear":0} for c in CURRENCY_SIDES}
    for p in pair_results:
        sig = p.get("bias","neutral")
        if sig=="neutral": continue
        for ccy, pairs in CURRENCY_SIDES.items():
            for psym, side in pairs:
                if psym==p.get("_sym",""):
                    vote = sig if side=="base" else ("bear" if sig=="bull" else "bull")
                    votes[ccy][vote] += 1
    return {c:("bull" if v["bull"]>v["bear"] else "bear" if v["bear"]>v["bull"] else "neutral")
            for c,v in votes.items()}

def fmt(price):
    return f"{price:.2f}" if price>20 else f"{price:.5f}"

def process_group_data(group, raw_data):
    results = []
    for sym in group:
        d = raw_data.get(sym, {})
        price = parse_price(d.get("price", {}))
        if price is None:
            print(f"  {display(sym)}: SKIP (no price)"); continue

        chg    = parse_change(d.get("quote", {}))
        pivots = calc_pivots(d.get("ohlc", {}))
        ma20   = parse_ma(d.get("ma20", {}))
        ma50   = parse_ma(d.get("ma50", {}))
        ma200  = parse_ma(d.get("ma200", {}))
        rsi    = parse_rsi(d.get("rsi", {}))

        ma_sig, ma_bull = ma_bias(price, ma20, ma50, ma200)
        bias     = overall_bias(price, pivots, ma_sig, rsi)
        priority = bias!="neutral" and ma_sig in ("bull","bear") and bias==ma_sig
        note     = generate_note(price, pivots, ma_bull, rsi, bias, chg)

        r = {
            "_sym":       sym,
            "pair":       display(sym),
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
            if bias=="bear":
                r["entry"]=fmt(price); r["sl"]=fmt(pivots["r1"]); r["tp1"]=fmt(pivots["s1"])
            elif bias=="bull":
                r["entry"]=fmt(price); r["sl"]=fmt(pivots["s1"]); r["tp1"]=fmt(pivots["r1"])

        results.append(r)
        print(f"  {display(sym)}: bias={bias} RSI={rsi} MA={ma_sig} pivot={'ok' if pivots else 'none'}")
    return results

def main():
    print("=== Technical Analysis Generator ===")
    print(f"Plan: Basic 8 | Pairs: {len(PAIRS)} | Pivot: calculated from OHLC daily")
    print(f"Estimated time: ~10 minutes\n")

    raw_a = fetch_group(GROUP_A, "A (pairs 1-8)")
    print("\n  Waiting 65s before Group B...")
    time.sleep(65)
    raw_b = fetch_group(GROUP_B, "B (pairs 9-16)")

    print("\nProcessing results...")
    results = process_group_data(GROUP_A, raw_a) + process_group_data(GROUP_B, raw_b)

    cbias = currency_bias([{**r, "_sym": next(
        (s for s in PAIRS if display(s)==r["pair"]), "")} for r in results])
    for r in results: r.pop("_sym", None)

    output = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "currency_bias": cbias,
        "pairs":         results,
    }

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n=== Done — {len(results)}/{len(PAIRS)} pairs ===")
    print(f"Priority setups: {sum(1 for r in results if r.get('priority'))}")
    print(f"Currency bias: {cbias}")

if __name__ == "__main__":
    main()
