import requests, csv, os, datetime, time

# --- ADD YOUR COINMARKETCAP API KEY HERE ---
CMC_API_KEY = "API_KEY_GOES_HERE"

def short_num(n):
    """Convert large numbers to short form like 1.2M or 3.4B"""
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    elif n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    elif n >= 1_000:
        return f"{n/1_000:.2f}K"
    else:
        return str(round(n))

def fetch_from_coingecko():
    """Fetch data from Coingecko (multiple pages to include small caps)"""
    tokens = []
    for page in range(1, 5):  # 4 pages × 250 = 1000 coins
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 250,
            "page": page,
            "sparkline": "false"
        }
        try:
            resp = requests.get(url, params=params, timeout=20)
            data = resp.json()
        except Exception as e:
            print(f"❌ Coingecko error (page {page}):", e)
            continue

        for token in data:
            symbol = token.get("symbol", "").upper()
            volume = token.get("total_volume", 0)
            marketcap = token.get("market_cap", 0)
            chain = token.get("asset_platform_id") or "Unknown"
            if marketcap and (volume > 0.70 * marketcap):
                tokens.append({
                    "source": "CoinGecko",
                    "symbol": symbol,
                    "marketcap": marketcap,
                    "volume": volume,
                    "chain": chain.title()
                })
        time.sleep(1)
    return tokens

def fetch_from_coinmarketcap():
    """Fetch data from CoinMarketCap (multiple pages for wider range)"""
    tokens = []
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
    for start in range(1, 1000, 250):  # 1 → 1000
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
        params = {"start": start, "limit": 250, "convert": "USD"}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=20)
            data = resp.json().get("data", [])
        except Exception as e:
            print(f"❌ CMC error (start={start}):", e)
            continue

        for token in data:
            symbol = token.get("symbol", "").upper()
            quote = token.get("quote", {}).get("USD", {})
            volume = quote.get("volume_24h", 0)
            marketcap = quote.get("market_cap", 0)
            platform = token.get("platform", {})
            chain = platform.get("name") if isinstance(platform, dict) else "Unknown"
            if marketcap and (volume > 0.5 * marketcap):
                tokens.append({
                    "source": "CoinMarketCap",
                    "symbol": symbol,
                    "marketcap": marketcap,
                    "volume": volume,
                    "chain": chain or "Unknown"
                })
        time.sleep(1)
    return tokens

def fetch_and_save():
    """Fetch from both APIs and save filtered tokens to CSV"""
    print(" Fetching from CoinGecko and CoinMarketCap...")

    cg_tokens = fetch_from_coingecko()
    cmc_tokens = fetch_from_coinmarketcap()

    # Combine and deduplicate by symbol
    all_tokens = {t["symbol"]: t for t in cg_tokens + cmc_tokens}
    hot_tokens = sorted(all_tokens.values(), key=lambda x: x["volume"], reverse=True)

    save_path = "/sdcard/Download"

    # Add date prefix to filename
    date_prefix = datetime.datetime.now().strftime("%d-%m-%y")
    csv_file = os.path.join(save_path, f"{date_prefix} volume gecko x cmc hunting.csv")

    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Source", "Symbol", "Marketcap", "Volume", "Chain"])
        for t in hot_tokens:
            writer.writerow([
                t["source"],
                t["symbol"],
                short_num(t["marketcap"]),
                short_num(t["volume"]),
                t["chain"]
            ])

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n✅ Updated {len(hot_tokens)} tokens — {now}")
    print(f"📁 Saved: {csv_file}\n")

# --- Auto-update loop (runs forever) ---
print("🚀 Auto-updating token list every 1 hour...")
while True:
    fetch_and_save()
    print("⏳ Waiting 1 hour before next update...\n")
    time.sleep(3600)
