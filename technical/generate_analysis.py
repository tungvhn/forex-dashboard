"""
generate_analysis.py
Uses TwelveData batch endpoints (comma-separated symbols) to fetch
price, pivot_points, MA20/50/200, RSI for all pairs in ~7 API calls total.
Writes technical/analysis.json with Vietnamese notes.

Env var required: TWELVE_API_KEY
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

def td_batch(path, extra_params, symbols):
    """
    Fetch a TwelveData indicator for multiple symbols in one call.
    Returns dict keyed by symbol, or {} on error.
    """
    sym_str = ",".join(symbols)
    qs = urllib.parse.urlencode({**extra_params, "symbol": sym_str, "apikey": TWELVE_KEY})
    url = f"https://api.twelvedata.com/{path}?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read())
        if isinstance(data, dict) and data.get("status") == "error":
            print(f"  TD error [{path}]: {data.get('message','')}")
            return {}
        # Single symbol returns data directly; multiple returns dict keyed by symbol
        if len(symbols) == 1:
            return {symbols[0]: data}
        return data
    except Exception as e:
        print(f"  TD fetch error [{path}]: {e}")
        return {}

def parse_price(raw):
    try: return float(raw.get("price", raw))
    except: return None

def parse_change(raw):
    try: return round(float(raw.get("percent_change", 0)), 4)
    except: return 0.0

def parse_pivots(raw):
    try:
        v = raw["values"][0] if "values" in raw else raw
        return {k: float(v[k]) for k in ["s2","s1","pp","r1","r2"]}
    except: return None

def parse_ma(raw):
    try:
        v = raw["values"][0] if "values" in raw else raw
        return float(v.get("ma", v))
    except: return None

def parse_rsi(raw):
    try:
        v = raw["values"][0] if "values" in raw else raw
        return round(float(v.get("rsi", v)), 1)
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
        if price_f >= r2:       parts.append("Vượt R2")
        elif price_f >= r1:     parts.append("Giữa R1–R2")
        elif price_f >= pp:
            ratio = (price_f-pp)/(r1-pp) if r1!=pp else 0
            parts.append("Sát R1" if ratio>0.75 else "Trên PP")
        elif price_f >= s1:
            ratio = (pp-price_f)/(pp-s1) if pp!=s1 else 0
            parts.append("Sát PP từ dưới" if ratio<0.25 else "Dưới PP")
        elif price_f >= s2:     parts.append("Giữa S1–S2")
        else:                   parts.append("Phá S2")

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

    parts.append("→ bias tăng" if bias=="bull" else "→ bias giảm" if bias=="bear" else "→ chờ tín hiệu")
    return ", ".join(parts)

def currency_bias(pair_results):
    votes = {c:{"bull":0,"bear":0} for c in CURRENCY_SIDES}
    for p in pair_results:
        sig = p.get("bias","neutral")
        if sig=="neutral": continue
        for ccy, pairs in CURRENCY_SIDES.items():
            for psym, side in pairs:
                if psym==p["_sym"]:
                    vote = sig if side=="base" else ("bear" if sig=="bull" else "bull")
                    votes[ccy][vote] += 1
    return {c:("bull" if v["bull"]>v["bear"] else "bear" if v["bear"]>v["bull"] else "neutral")
            for c,v in votes.items()}

def fmt(price):
    return f"{price:.2f}" if price>20 else f"{price:.5f}"

def main():
    syms = PAIRS
    print(f"Fetching batch data for {len(syms)} pairs...")

    # --- Batch fetch (each call = 1 API request regardless of symbol count) ---
    print("  [1/7] prices"); time.sleep(1)
    prices_raw  = td_batch("price",  {}, syms)
    time.sleep(8)

    print("  [2/7] quotes (% change)"); 
    quotes_raw  = td_batch("quote",  {}, syms)
    time.sleep(8)

    print("  [3/7] pivot points")
    pivots_raw  = td_batch("pivot_points", {"interval":"1day","outputsize":1}, syms)
    time.sleep(8)

    print("  [4/7] MA20")
    ma20_raw    = td_batch("ma", {"interval":"4h","time_period":20,"outputsize":1}, syms)
    time.sleep(8)

    print("  [5/7] MA50")
    ma50_raw    = td_batch("ma", {"interval":"4h","time_period":50,"outputsize":1}, syms)
    time.sleep(8)

    print("  [6/7] MA200")
    ma200_raw   = td_batch("ma", {"interval":"4h","time_period":200,"outputsize":1}, syms)
    time.sleep(8)

    print("  [7/7] RSI")
    rsi_raw     = td_batch("rsi", {"interval":"4h","time_period":14,"outputsize":1}, syms)

    print("\nComputing signals...")
    results = []

    for sym in syms:
        dname = display(sym)

        price   = parse_price(prices_raw.get(sym, {}))
        chg     = parse_change(quotes_raw.get(sym, {}))
        pivots  = parse_pivots(pivots_raw.get(sym, {}))
        ma20    = parse_ma(ma20_raw.get(sym, {}))
        ma50    = parse_ma(ma50_raw.get(sym, {}))
        ma200   = parse_ma(ma200_raw.get(sym, {}))
        rsi     = parse_rsi(rsi_raw.get(sym, {}))

        if price is None:
            print(f"  {dname}: SKIP (no price)")
            continue

        ma_sig, ma_bull = ma_bias(price, ma20, ma50, ma200)
        bias     = overall_bias(price, pivots, ma_sig, rsi)
        priority = bias!="neutral" and ma_sig in ("bull","bear") and bias==ma_sig
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
            if bias=="bear":
                r["entry"]=fmt(price); r["sl"]=fmt(pivots["r1"]); r["tp1"]=fmt(pivots["s1"])
            elif bias=="bull":
                r["entry"]=fmt(price); r["sl"]=fmt(pivots["s1"]); r["tp1"]=fmt(pivots["r1"])

        results.append(r)
        print(f"  {dname}: bias={bias} RSI={rsi} MA={ma_sig} pivot={'ok' if pivots else 'none'}")

    cbias = currency_bias([{**r,"_sym":next((s for s in PAIRS if display(s)==r["pair"]),"")} for r in results])
    for r in results: r.pop("_sym",None)

    output = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "currency_bias": cbias,
        "pairs":         results,
    }

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis.json")
    with open(path,"w",encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nDone — {len(results)}/{len(PAIRS)} pairs written")
    print(f"Priority setups: {sum(1 for r in results if r.get('priority'))}")
    print(f"Currency bias: {cbias}")

if __name__=="__main__":
    main()
