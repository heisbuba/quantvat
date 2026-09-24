import time
import datetime
import html
import threading
from pathlib import Path
from string import Template
import asyncio
import aiohttp
from typing import Any

from src.state import get_user_temp_dir, update_progress, set_pending_file
from src.config import STABLECOINS
from src.services.utils import short_num, now_str, safe_float, safe_int_clamped, parse_mc

# "Pages to Scan" bounds - keep in sync with the min/max on the home.html input
SCAN_PAGES_DEFAULT = 6
SCAN_PAGES_MIN = 1
SCAN_PAGES_MAX = 8

REQUEST_TIMEOUT = aiohttp.ClientTimeout(connect=5, total=20)

# Analysis Report Source 
SPOT_REPORT_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "reports" / "spot_report.html"


def spot_volume_tracker(user_keys: dict[str, Any], user_id: str | int) -> None:

    print("    Starting fresh spot analysis...")
    threading.current_thread().name = f"user_{user_id}"
    update_progress(user_id, 10, "Starting spot market scan...", "active")

    COINGECKO_API_KEY = user_keys.get("COINGECKO_API_KEY", "CONFIG_REQUIRED_CG")
    settings = user_keys.get("engine_settings", {})
    MIN_VTMR = safe_float(settings.get('min_vtmr'), 0.5)
    MAX_VTMR = safe_float(settings.get('max_vtmr'), 199.0)
    MIN_LC_VTMR = safe_float(settings.get('min_largecap_vtmr'), 0.5)
    MIN_S_MC = parse_mc(settings.get('min_s_mc'), 0.0)
    MAX_S_MC = parse_mc(settings.get('max_s_mc'), float('inf'))
    SCAN_PAGES = safe_int_clamped(settings.get('scan_pages'), SCAN_PAGES_DEFAULT, SCAN_PAGES_MIN, SCAN_PAGES_MAX)
    LC_THRESHOLD = 1_000_000_000
    FETCH_THRESHOLD = min(MIN_VTMR, MIN_LC_VTMR)

    STEALTH_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }

    def create_html_report(hot_tokens: list[dict[str, Any]]) -> Any:
        date_prefix = datetime.datetime.now().strftime("%b-%d-%y")
        user_dir = get_user_temp_dir(user_id)
        html_file = user_dir / f"Spot_Analysis_{date_prefix}.html"
        current_time = now_str("%d-%m-%Y %H:%M:%S")

        max_flip = max((t.get('flipping_multiple', 0) for t in hot_tokens), default=0)
        large_cap_count = len([t for t in hot_tokens if t.get('large_cap')])

        rows_html = []
        for i, token in enumerate(hot_tokens):
            is_lc = token.get('large_cap', False)
            row_class = "large-cap" if is_lc else ""
            vtmr = token.get('flipping_multiple', 0)
            vol_class = "vol-high" if vtmr >= 2 else ""
            sym = html.escape(str(token.get('symbol', '???')), quote=True)
            coin_id = html.escape(token.get('coin_id', ''), quote=True)

            change_24h = token.get('change_24h')
            if change_24h is None:
                pcp_html = '<span class="pcp-flat">n/a</span>'
            else:
                pcp_class = "pcp-pos" if change_24h > 0 else ("pcp-neg" if change_24h < 0 else "pcp-flat")
                pcp_sign = "+" if change_24h > 0 else ""
                pcp_html = f'<span class="{pcp_class}">{pcp_sign}{change_24h:.2f}%</span>'

            data_attrs = {
                "coin-id": coin_id,
                "symbol": sym,
                "price": token.get('price', 0),
                "mcap": token.get('marketcap', 0),
                "vol24": token.get('volume', 0),
                "vtmr": vtmr,
                "c24": token.get('change_24h', 0),
            }
            data_str = " ".join(f'data-{k}="{v}"' for k, v in data_attrs.items())

            rows_html.append(f"""
                <tr class="{row_class}" {data_str}>
                    <td style="text-align:center; color:var(--text-dim);" class="mono">#{i+1}</td>
                     <td><a href="/deep-diver?ticker={sym}" class="ticker-btn">{sym}</a></td>
                    <td style="padding-left:5px;" class="mono hide-on-mobile">{pcp_html}</td>
                    <td style="padding-left:5px;" class="mono">${short_num(token.get('marketcap', 0))}</td>
                    <td style="padding-left:5px;" class="mono">${short_num(token.get('volume', 0))}</td>
                    <td class="mono {vol_class}" style="padding-left:5px;">{vtmr:.2f}x</td>
                    <td style="text-align:center;">
                        <button class="action-btn analyze-btn" onclick="handleAction(event, this)">Summary</button>
                    </td>
                </tr>
            """)

        table_rows = "\n".join(rows_html)

        template_text = SPOT_REPORT_TEMPLATE.read_text(encoding="utf-8")
        html_content = Template(template_text).substitute(
            current_time=current_time,
            token_count=len(hot_tokens),
            large_cap_count=large_cap_count,
            max_flip=f"{max_flip:.1f}",
            table_rows=table_rows,
        )

        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_content)
        return html_file

    # ------------------------------------------------------------------
    # Async data fetching 
    # ------------------------------------------------------------------
    async def _fetch_cg_page(session: aiohttp.ClientSession, page: int, headers: dict) -> tuple[int | None, list]:
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 250,
            "page": page,
            "price_change_percentage": "24h",
        }
        try:
            async with session.get("https://api.coingecko.com/api/v3/coins/markets", params=params, headers=headers) as resp:
                if resp.status == 200:
                    return resp.status, await resp.json()
                return resp.status, []
        except Exception:
            return None, []

    async def fetch_coingecko(session: aiohttp.ClientSession) -> list[dict[str, Any]]:
        use_key = bool(COINGECKO_API_KEY and COINGECKO_API_KEY != "CONFIG_REQUIRED_CG")
        headers = STEALTH_HEADERS.copy()
        if use_key:
            headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

        page_range = range(1, SCAN_PAGES + 1)
        results = list(await asyncio.gather(*[_fetch_cg_page(session, p, headers) for p in page_range]))

        retry_pages = [p for p, (status, _) in zip(page_range, results) if use_key and status in (401, 403, 429)]
        if retry_pages:
            retry_results = await asyncio.gather(*[_fetch_cg_page(session, p, STEALTH_HEADERS) for p in retry_pages])
            for p, res in zip(retry_pages, retry_results):
                results[p - 1] = res
                
        tokens = []
        for _, page_data in results:
            for t in page_data:
                symbol = (t.get("symbol") or "").upper()
                if symbol in STABLECOINS:
                    continue
                vol = safe_float(t.get("total_volume"), 0.0)
                mc = safe_float(t.get("market_cap"), 0.0)
                if mc <= 0:
                    continue
                ratio = vol / mc
                if ratio < FETCH_THRESHOLD:
                    continue

                coin_id = t.get("id", "")
                price = safe_float(t.get("current_price"), None)
                pcp = t.get("price_change_percentage_24h")
                change_24h = safe_float(pcp, None)

                tokens.append({
                    "coin_id": coin_id,
                    "symbol": symbol,
                    "marketcap": mc,
                    "volume": vol,
                    "price": price,
                    "change_24h": change_24h,
                })
        print(f"    CoinGecko returned: {len(tokens)} tokens")
        return tokens

    async def _fetch_all_sources_async() -> list[dict[str, Any]]:
        print("    Volume-driven scan (filters applied)...")
        connector = aiohttp.TCPConnector(limit=10)
        async with aiohttp.ClientSession(connector=connector, timeout=REQUEST_TIMEOUT) as session:
            cg_tokens = await fetch_coingecko(session)
            update_progress(user_id, 50, "Data fetched, processing...", "active")
            return cg_tokens

    def fetch_all_sources() -> list[dict[str, Any]]:
        return asyncio.run(_fetch_all_sources_async())

    t0 = time.perf_counter()
    cg_tokens = fetch_all_sources()
    print(f"    Total time taken: {time.perf_counter() - t0:.2f}s")
    update_progress(user_id, 60, "Cross-referencing and verifying tokens...", "active")

    verified_tokens = []
    for t in cg_tokens:
        ratio = t['volume'] / t['marketcap']
        is_large = t['marketcap'] > LC_THRESHOLD
        mc_ok = MIN_S_MC <= t['marketcap'] <= MAX_S_MC
        vtmr_ok = (is_large and MIN_LC_VTMR <= ratio <= MAX_VTMR) or \
                  (not is_large and MIN_VTMR <= ratio <= MAX_VTMR)
        if vtmr_ok and mc_ok:
            verified_tokens.append({
                "coin_id": t['coin_id'],
                "symbol": t['symbol'],
                "marketcap": t['marketcap'],
                "volume": t['volume'],
                "price": t['price'],
                "flipping_multiple": ratio,
                "large_cap": is_large,
                "change_24h": t.get('change_24h'),
            })

    hot_tokens = sorted(verified_tokens, key=lambda x: x["flipping_multiple"], reverse=True)
    update_progress(user_id, 90, "Compiling spot report...", "active")

    html_file = create_html_report(hot_tokens)
    set_pending_file(user_id, "spot", html_file)

    report_filename = html_file.name
    now_h = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"    Found {len(hot_tokens)} filtered tokens at {now_h}")
    print(f"    Report saved as: {report_filename}")
    print("    Spot analysis completed!")