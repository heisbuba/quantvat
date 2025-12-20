import re
import pandas as pd
import datetime
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any
from pathlib import Path
import config
from utils import SESSION, now_str

try:
    import pypdf
except ImportError:
    pypdf = None

# ==============================
# HELPER FUNCTIONS (FROM ORIGINAL)
# ==============================
def oi_score_and_signal(oi_change: float) -> Tuple[int, str]:
    if oi_change > 0.20: return 5, "Strong"
    if oi_change > 0.10: return 4, "Bullish"
    if oi_change > 0.00: return 3, "Build-Up"
    if oi_change > -0.10: return 2, "Weakening"
    if oi_change > -0.20: return 1, "Exiting"
    return 0, "Exiting"

def funding_score_and_signal(funding_val: float) -> Tuple[str, str]:
    if funding_val >= 0.05: return "Greed", "oi-strong"
    if funding_val > 0.00: return "Bullish", "oi-strong"
    if funding_val <= -0.05: return "Extreme Fear", "oi-weak"
    if funding_val < 0.00: return "Bearish", "oi-weak"
    return "Neutral", ""

def make_oiss(oi_percent_str: str) -> str:
    if not oi_percent_str: return "-"
    val = oi_percent_str.replace("%", "").strip()
    try:
        oi_change = float(val) / 100
        score, signal = oi_score_and_signal(oi_change)
        
        if oi_change > 0: css_class = "oi-strong"
        elif oi_change < 0: css_class = "oi-weak"
        else: css_class = ""

        sign = "+" if oi_change > 0 else ""
        
        if css_class:
            percent = f'<span class="{css_class}">{sign}{oi_change*100:.0f}%</span>'
        else:
            percent = f"{sign}{oi_change*100:.0f}%"

        return f"{percent} {signal}"
    except Exception:
        return "-"

def make_funding_signal(funding_str: str) -> str:
    if not funding_str or funding_str in ['-', 'N/A']: return "-"
    try:
        val = float(funding_str.replace('%', '').strip())
        signal_word, css_class = funding_score_and_signal(val)
        
        if css_class:
            html = f'<span class="{css_class}">{val}%</span> <span style="font-size:0.8em; color:#7f8c8d;">{signal_word}</span>'
        else:
            html = f'{val}% {signal_word}'
        return html
    except Exception:
        return funding_str

@dataclass
class TokenData:
    ticker: str
    name: str
    market_cap: str
    volume: str
    vtmr: float
    funding: str = "-"
    oiss: str = "-"

# ==============================
# PDF PARSER (FULL ORIGINAL LOGIC)
# ==============================
class PDFParser:
    FINANCIAL_PATTERN = re.compile(
        r'(\$?[+-]?[\d,\.]+[kKmMbB]?)\s+'             
        r'(\$?[+-]?[\d,\.]+[kKmMbB]?)\s+'             
        r'(?:([+\-]?[\d\.\,]+\%?|[\-\–\—]|N\/A)\s+)?' 
        r'(?:([+\-]?[\d\.\,]+\%?|[\-\–\—]|N\/A)\s+)?' 
        r'(\d*\.?\d+)'                                
    )

    IGNORE_KEYWORDS = {
        'page', 'coinalyze', 'contract', 'filter', 'column',
        'mkt cap', 'vol 24h', 'vtmr', 'coins', 'all contracts', 'custom metrics', 'watchlists'
    }

    @classmethod
    def extract(cls, path: Path) -> pd.DataFrame:
        print(f"   Parsing Futures PDF: {path.name}")
        if pypdf is None:
            print("   pypdf not available - PDF parsing disabled.")
            return pd.DataFrame()
        data: List[TokenData] = []
        try:
            reader = pypdf.PdfReader(path)
            for page in reader.pages:
                raw = page.extract_text() or ""
                lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
                page_data = cls._parse_page_smart(lines)
                data.extend(page_data)
            print(f"   Extracted {len(data)} futures tokens")
            if not data:
                return pd.DataFrame()
            df = pd.DataFrame([vars(t) for t in data])
            df['ticker'] = df['ticker'].apply(lambda x: re.sub(r'[^A-Z0-9]', '', str(x).upper()))
            df = df[df['ticker'].str.len() > 1]
            print(f"   Valid futures tokens: {len(df)}")
            return df
        except Exception as e:
            print(f"   PDF Error: {e}")
            return pd.DataFrame()

    @classmethod
    def _parse_page_smart(cls, lines: List[str]) -> List[TokenData]:
        financials = []
        raw_text_lines = []
        
        for line in lines:
            if any(k in line.lower() for k in cls.IGNORE_KEYWORDS):
                continue
            
            fin_match = cls.FINANCIAL_PATTERN.search(line)
            if fin_match:
                groups = fin_match.groups()
                mc = groups[0].replace('$', '').replace(',', '')
                vol = groups[1].replace('$', '').replace(',', '')
                oi_str = groups[2]       # Group 3: OI
                fund_str = groups[3]     # Group 4: Funding
                vtmr = groups[4]         # Group 5: VTMR
                
                try:
                    float(vtmr)
                    financials.append((mc, vol, vtmr, oi_str, fund_str))
                except:
                    raw_text_lines.append(line)
            else:
                if not line.isdigit() and len(line) > 1:
                    raw_text_lines.append(line)
        
        token_pairs = []
        i = 0
        while i < len(raw_text_lines):
            line = raw_text_lines[i]
            clean_current = cls._clean_ticker_strict(line)
            
            if clean_current:
                if i + 1 < len(raw_text_lines):
                    next_line = raw_text_lines[i + 1]
                    clean_next = cls._clean_ticker_strict(next_line)
                    if clean_next:
                        token_pairs.append((line, clean_next))
                        i += 2
                        continue
            
            if i + 1 < len(raw_text_lines):
                name_candidate = raw_text_lines[i]
                ticker_candidate_raw = raw_text_lines[i + 1]
                ticker = cls._clean_ticker_strict(ticker_candidate_raw)
                if ticker:
                    token_pairs.append((name_candidate, ticker))
                    i += 2
                else:
                    i += 1
            else:
                i += 1
        
        tokens: List[TokenData] = []
        limit = min(len(token_pairs), len(financials))
        
        for k in range(limit):
            name, ticker = token_pairs[k]
            mc, vol, vtmr, oi_pct, fund_pct = financials[k]

            oiss_val = make_oiss(oi_pct) if oi_pct and oi_pct not in ['-', 'N/A'] else "-"
            funding_val = make_funding_signal(fund_pct)

            tokens.append(TokenData(
                ticker=ticker,
                name=name,
                market_cap=mc,
                volume=vol,
                vtmr=float(vtmr),
                funding=funding_val,
                oiss=oiss_val
            ))
        return tokens

    @staticmethod
    def _clean_ticker_strict(text: str) -> Optional[str]:
        if len(text) > 15:
            return None
        cleaned = re.sub(r'[^A-Z0-9]', '', text.upper())
        if 2 <= len(cleaned) <= 12: 
            return cleaned
        return None

# ==============================
# DATA PROCESSOR (FULL ORIGINAL LOGIC)
# ==============================
class DataProcessor:
    @staticmethod
    def load_spot(path: Path) -> pd.DataFrame:
        print(f"   Parsing Spot File: {path.name}")
        try:
            if path.suffix == '.html':
                df = pd.read_html(path)[0]
            else:
                df = pd.read_csv(path)
            df.columns = [c.lower().replace(' ', '_') for c in df.columns]
            
            col_map = {
                'ticker': 'ticker',
                'symbol': 'ticker', 
                'spot_vtmr': 'spot_flip', 
                'flipping_multiple': 'spot_flip',
                'market_cap': 'spot_mc',
                'marketcap': 'spot_mc',
                'volume_24h': 'spot_vol',
                'volume': 'spot_vol'
            }
            
            df = df.rename(columns=col_map, errors='ignore')
            
            if 'ticker' not in df.columns:
                 for col in df.columns:
                    if 'sym' in col or 'tick' in col or 'tok' in col:
                        df = df.rename(columns={col: 'ticker'})
                        break

            if 'ticker' in df.columns:
                df['ticker'] = df['ticker'].apply(lambda x: re.sub(r'[^A-Z0-9]', '', str(x).upper()))
            print(f"   Extracted {len(df)} spot tokens")
            return df
        except Exception as e:
            print(f"   Spot File Error: {e}")
            return pd.DataFrame()

    @staticmethod
    def _generate_table_html(title: str, df: pd.DataFrame, headers: List[str], df_cols: List[str]) -> str:
        if df.empty:
            return f'<div class="table-container"><h2>{title}</h2><p>No data found</p></div>'
        missing = [c for c in df_cols if c not in df.columns]
        df_display = df.copy()
        for m in missing:
            df_display[m] = ""
        df_display = df_display[df_cols]
        df_display.columns = headers
        table_html = df_display.to_html(index=False, classes='table', escape=False)
        return f'<div class="table-container"><h2>{title}</h2>{table_html}</div>'

    @staticmethod
    def generate_html_report(futures_df: pd.DataFrame, spot_df: pd.DataFrame, user_id: str) -> Optional[str]:
        if futures_df.empty or spot_df.empty:
            return None
        
        if 'oiss' not in futures_df.columns: futures_df['oiss'] = "-"

        valid_futures = futures_df.copy()
        try:
            if 'vtmr' in valid_futures.columns:
                valid_futures = valid_futures[valid_futures['vtmr'] >= 0.50]
                valid_futures['vtmr_display'] = valid_futures['vtmr'].apply(lambda x: f"{x:.1f}x")
        except Exception as e:
            print(f"   Futures high-quality filtering error: {e}")
            valid_futures['vtmr_display'] = valid_futures['vtmr']

        merged = pd.merge(spot_df, valid_futures, on='ticker', how='inner', suffixes=('_spot', '_fut'))
        if 'vtmr' in merged.columns: merged = merged.sort_values('vtmr', ascending=False)
        
        futures_only = valid_futures[~valid_futures['ticker'].isin(spot_df['ticker'])].copy()
        if 'vtmr' in futures_only.columns: futures_only = futures_only.sort_values('vtmr', ascending=False)
        
        spot_only = spot_df[~spot_df['ticker'].isin(merged['ticker'])].copy()
        
        if 'spot_flip' in spot_only.columns:
            try:
                spot_only = spot_only.copy()
                spot_only.loc[:, 'flip_numeric'] = spot_only['spot_flip'].astype(str).str.replace('x', '', case=False).astype(float)
                spot_only = spot_only[spot_only['flip_numeric'] >= 0.50]
                spot_only = spot_only.sort_values('flip_numeric', ascending=False)
            except Exception: pass
        
        ORIGINAL_MATCHED_HEADERS = ["Ticker", "Spot Market Cap", "Spot Volume", "Spot VTMR", "Futures Volume", "Futures VTMR", "OISS", "Funding"]
        ORIGINAL_FUTURES_HEADERS = ["Token", "Market Cap", "Volume", "VTMR", "OISS", "Funding"]
        ORIGINAL_SPOT_HEADERS = ["Ticker", "Market Cap", "Volume", "Spot VTMR"]

        merged_cols = ['ticker', 'spot_mc', 'spot_vol', 'spot_flip', 'volume', 'vtmr_display', 'oiss', 'funding']
        futures_cols = ['ticker', 'market_cap', 'volume', 'vtmr_display', 'oiss', 'funding']
        
        html_content = ""
        html_content += DataProcessor._generate_table_html("Tokens in Both Futures & Spot Markets", merged, ORIGINAL_MATCHED_HEADERS, merged_cols)
        html_content += DataProcessor._generate_table_html("Remaining Futures-Only Tokens", futures_only, ORIGINAL_FUTURES_HEADERS, futures_cols)
        html_content += DataProcessor._generate_table_html("Remaining Spot-Only Tokens", spot_only, ORIGINAL_SPOT_HEADERS, ['ticker', 'spot_mc', 'spot_vol', 'spot_flip'])
        
        # CHEAT SHEET FOOTER
        cheat_sheet_pdf_footer = """
            <div style="margin-top: 30px; padding: 15px; background: #ecf0f1; border-radius: 8px;">
                <h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 5px; margin-top: 0;">OISS & Funding Cheat Sheet:</h2>
                <ul style="list-style-type: none; padding-left: 0; line-height: 1.6;">                
<li><strong>(1) Bullish Squeeze:</strong> Open Interest is positive and Funding Rate is negative, meaning new capital is flowing in while most are short. Shorts rush to buy back, triggering a sharp price surge. Bro, we are bullish.</li>
<li><strong>(2) Uptrend:</strong> Open Interest is positive and Funding Rate is positive, meaning the market is rising with broad participation. Capital keeps flowing, but the trend is costly (high fees). Solid uptrend, but watch for pauses.</li>
<li><strong>(3) Short Covering / Recovery:</strong> Open Interest is negative and Funding Rate is negative, meaning shorts are closing positions, causing a temporary rebound. No real buying pressure—trend may not last.</li>
<li><strong>(4) Flatline:</strong> Open Interest is unchanged and Funding Rate is positive, meaning the market is dead with no new capital. Minimal movement, trend paused, fees still accumulate.</li>
<li><strong>(5) Bearish Dump:</strong> Open Interest is negative and Funding Rate is positive, meaning longs are exiting aggressively or facing liquidation. Price drops sharply—strong selling pressure dominates.</li><br/>
<li><strong style="color:red;">NOTE:</strong> OISS stands for <strong>Open Interest Signal Score</strong> and FUNDING stands for <strong>Funding Rate</strong>.</li>
</ul>
<h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 5px; margin-top: 0;">Why VTMR of All Sides Matter</h2>
                <ul style="list-style-type: none; padding-left: 0; line-height: 1.6;">  
<li>  
<strong>(1) The Divergence Signal (Spot vs. Futures):</strong>
<ul style="list-style-type: disc; padding-left: 20px; line-height: 1.4;">
  <li><strong>Futures VTMR &gt; Spot VTMR (The Casino):</strong> If Futures volume is huge (e.g., 8x) but Spot is low, the price is being driven by leverage and speculation. This is fragile—expect violent "wicks" and liquidation hunts.</li>
  <li><strong>Spot VTMR &gt; Futures VTMR (The Bank):</strong> If Spot volume is leading, real money is buying to own the asset, not just gamble on it. This signals genuine accumulation and a healthier, more sustainable trend.</li>
</ul>
</li>  
<li>  
<strong>(2) The Heat Check:</strong>
<ul style="list-style-type: disc; padding-left: 20px; line-height: 1.4;">
  <li>If VTMR is Over 1.0x: The token is trading its entire Market Cap in volume. It is hyper-active and volatile. But still that doesn't guarantee pump.</li>
</ul>
</li>  
</ul>
</ul>
                <h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 5px; margin-top: 20px;">Remaining Spot Only Tokens</h2>
                <p>Remember those remaining spot only tokens because there is plenty opportunity there too. So, check them out. Don't fade on them.</p>
       <h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 5px; margin-top: 20px;">Disclaimer</h2>
                <small>This analysis was generated by you using the <strong>Crypto Volume Analysis Toolkit</strong> by <strong>@heisbuba</strong>. It empowers your market research but does not replace your due diligence. Verify the data, back your own instincts, and trade entirely at your own risk.</small>
            </div>
        """
        
        ORIGINAL_HTML_STYLE = """
            body { margin: 20px; background: #f5f5f5; font-family: Arial, sans-serif; }
            .table-container { margin: 20px 0; background: white; padding: 15px; border-radius: 10px; }
            table { width: 100%; border-collapse: collapse; margin: 10px 0; }
            th, td { padding: 10px; border: 1px solid #ddd; text-align: left; }
            th { background: #2c3e50; color: white; }
            tr:nth-child(even) { background: #f9f9f9; }
            .header { background: #2c3e50; color: white; padding: 20px; border-radius: 10px; text-align: center; }
            h2 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
            .footer { text-align: center; margin-top: 20px; color: #7f8c8d; }
            .oi-strong { color: #27ae60; font-weight: bold; }
            .oi-weak { color: #c0392b; }
        """

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Crypto Volume-driven Data Analysis Report</title>
            <meta charset="UTF-8">
            <style>{ORIGINAL_HTML_STYLE}</style>
        </head>
        <body>
            <div class="header">
                <h1>Cross-Market Crypto Analysis Report</h1>
                <p>Using Both Spot & Futures Market Data</p>
                <p><small>Generated on: {now_str()}</small></p>
            </div>
            {html_content}
              {cheat_sheet_pdf_footer}
            <div class="footer">
                <p>Generated by Crypto Volume Analysis Toolkit 5.0 | By (@heisbuba)</p>
            </div>
        </body>
        </html>
        """
        return html

def convert_html_to_pdf(html: str, output_dir: Path, user_id: str) -> Optional[Path]:
    print("\n   Converting to PDF...")
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    pdf_name = f"{today}-crypto-analysis.pdf"
    pdf_path = output_dir / pdf_name
    try:
        resp = SESSION.post(
            "https://api.html2pdf.app/v1/generate",
            json={'html': html, 'apiKey': config.HTML2PDF_API_KEY},
            headers={'Content-Type': 'application/json'},
            timeout=30
        )
        if resp.status_code == 200:
            with open(pdf_path, "wb") as f:
                f.write(resp.content)
            
            # [V5 UPGRADE]: Cloud Upload
            url = config.FirebaseHelper.upload_report(user_id, pdf_path)
            if url: print(f"   🚀 Cloud Backup: {url}")
            
            return pdf_path
        else:
            print(f"   API Error: {resp.status_code}")
    except Exception as e:
        print(f"   Error: {e}")
    return None

def cleanup_after_analysis(spot_file: Optional[Path], futures_file: Optional[Path]) -> int:
    files_cleaned = 0
    if spot_file and spot_file.exists():
        try:
            spot_file.unlink()
            print(f"   🗑️  Cleaned up spot file: {spot_file.name}")
            files_cleaned += 1
        except Exception: pass
    if futures_file and futures_file.exists():
        try:
            futures_file.unlink()
            print(f"   🗑️  Cleaned up futures PDF: {futures_file.name}")
            files_cleaned += 1
        except Exception: pass
    return files_cleaned

def run_futures_analysis(user_id: str):
    # Try to find the user's specific spot file in Uploads
    spot_file = None
    uploaded_futures = config.UPLOAD_FOLDER / f"{user_id}_futures.pdf"
    
    # Logic to find the correct spot file for this user
    for f in config.UPLOAD_FOLDER.iterdir():
        if f.is_file() and "spot" in f.name.lower() and f.suffix == ".html":
            # If we named it with user_id in spot_tracker, match it
            if f.name.startswith(user_id):
                spot_file = f
                break
    
    if not spot_file or not uploaded_futures.exists():
        print("   ❌ Missing Data. Upload Futures PDF and Run Spot Scan first.")
        return

    print(f"   📄 Using Futures: {uploaded_futures.name}")
    print(f"   📄 Using Spot: {spot_file.name}")

    f_df = PDFParser.extract(uploaded_futures)
    s_df = DataProcessor.load_spot(spot_file)
    
    html = DataProcessor.generate_html_report(f_df, s_df, user_id)
    
    if html:
        pdf_path = convert_html_to_pdf(html, config.UPLOAD_FOLDER, user_id)
        if pdf_path:
            print("   ✅ Analysis Complete.")
            print("   🧹 Cleaning up source files after analysis...")
            cleanup_after_analysis(spot_file, uploaded_futures)