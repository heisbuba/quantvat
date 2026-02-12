import re
import datetime
import pandas as pd
from typing import List, Optional, Tuple
from pathlib import Path

# Import our modular components
from ..state import get_user_temp_dir
from .utils import now_str, convert_html_to_pdf, cleanup_after_analysis
from .futures_engine import PDFParser

# --- Constants for Reporting ---
ORIGINAL_HTML_STYLE = """
    body { margin: 20px; background: #f5f5f5; font-family: Arial, sans-serif; }
    .table-container { margin: 20px 0; background: white; padding: 15px; border-radius: 10px; }
    table { width: 100%; border-collapse: collapse; margin: 10px 0; }
    thead { display: table-row-group; }
    th, td { padding: 10px; border: 1px solid #ddd; text-align: left; }
    th { background: #2c3e50; color: white; }
    tr:nth-child(even) { background: #f9f9f9; }
    .header { background: #2c3e50; color: white; padding: 20px; border-radius: 10px; text-align: center; }
    h2 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
    .footer { text-align: center; margin-top: 20px; color: #7f8c8d; }
    .oi-strong { color: #27ae60; font-weight: bold; }
    .oi-weak { color: #c0392b; }
"""

ORIGINAL_MATCHED_HEADERS = ["Ticker", "Spot MrktCap", "Spot Volume", "Spot VTMR", "Futures Volume", "Futures VTMR", "OISS", "Funding Rate"]
ORIGINAL_FUTURES_HEADERS = ["Ticker", "Market Cap", "Volume", "VTMR", "OISS", "Funding Rate"]
ORIGINAL_SPOT_HEADERS = ["Ticker", "MarketCap", "Volume", "VTMR"]

class FileScanner:
    """Locates the latest Spot and Futures data files in the USER directory."""
    @staticmethod
    def find_files(user_id) -> Tuple[Optional[Path], Optional[Path]]:
        spot_file: Optional[Path] = None
        futures_file: Optional[Path] = None
        
        user_dir = get_user_temp_dir(user_id)
        if not user_dir.exists():
            return None, None

        # Get today's date for filtering
        today = datetime.datetime.now().date()
        
        # Filter for today's files only
        today_files = []
        for f in user_dir.iterdir():
            if f.is_file():
                try:
                    file_time = datetime.datetime.fromtimestamp(f.stat().st_mtime)
                    if file_time.date() == today:  # Only use today's files
                        today_files.append(f)
                except Exception:
                    continue
        
        if not today_files:
            return None, None
            
        # Sort by modification time (newest first)
        files = sorted(today_files, key=lambda x: x.stat().st_mtime, reverse=True)

        for f in files:
            name = f.name.lower()
            if not futures_file and f.suffix == ".pdf" and "futures" in name:
                futures_file = f
            elif not spot_file and f.suffix in [".csv", ".html"] and "spot" in name:
                spot_file = f
            
            if spot_file and futures_file:
                break
                
        return spot_file, futures_file

class DataProcessor:
    """Handles Dataframe loading, merging, and HTML generation."""
    
    @staticmethod
    def load_spot(path: Path) -> pd.DataFrame:
        print(f"   Parsing Spot File: {path.name}")
        try:
            # Explicit UTF-8 for Unicode preservation
            if path.suffix == '.html':
                df = pd.read_html(str(path), encoding='utf-8')[0]
            else:
                df = pd.read_csv(path, encoding='utf-8')
            df.columns = [c.lower().replace(' ', '_') for c in df.columns]
            
            col_map = {
                'ticker': 'ticker',
                'symbol': 'ticker', 
                'vtmr': 'vtmr',           # <--- Ensures VTMR isn't blank
                'spot_vtmr': 'vtmr', 
                'flipping_multiple': 'vtmr',
                'market_cap': 'market_cap',
                'marketcap': 'market_cap',
                'volume_24h': 'volume',
                'volume': 'volume'
            }
            
            df = df.rename(columns=col_map, errors='ignore')
            
            # Normalize ticker column (Find it if it's missing)
            if 'ticker' not in df.columns:
                 for col in df.columns:
                    if 'sym' in col or 'tick' in col or 'tok' in col:
                        df = df.rename(columns={col: 'ticker'})
                        break

            # Unicode-safe cleaning (Protects Chinese characters)
            if 'ticker' in df.columns:
                df['ticker'] = df['ticker'].apply(lambda x: str(x).strip().upper())
                
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
        # escape=False is critical for rendering ticker links
        table_html = df_display.to_html(index=False, classes='table', escape=False)
        return f'<div class="table-container"><h2>{title}</h2>{table_html}</div>'

    @staticmethod
    def generate_html_report(futures_df: pd.DataFrame, spot_df: pd.DataFrame) -> Optional[str]:
        """Merges Spot and Futures dataframes and creates the final HTML report."""
        if futures_df.empty or spot_df.empty:
            return None
        
        if 'oiss' not in futures_df.columns:
            futures_df['oiss'] = "-"

        valid_futures = futures_df.copy()
        try:
            if 'vtmr' in valid_futures.columns:
                valid_futures = valid_futures[valid_futures['vtmr'] >= 0.50]
                valid_futures['vtmr_display'] = valid_futures['vtmr'].apply(lambda x: f"{x:.2f}x")
        except Exception as e:
            print(f"   Futures high-quality filtering error: {e}")
            valid_futures['vtmr_display'] = valid_futures['vtmr']

        # Suffix-based merge to prevent blank column mapping issues
        merged = pd.merge(spot_df, valid_futures, on='ticker', how='inner', suffixes=('_spot', '_fut'))
        if 'vtmr_fut' in merged.columns:
            merged = merged.sort_values('vtmr_fut', ascending=False)
        
        futures_only = valid_futures[~valid_futures['ticker'].isin(spot_df['ticker'])].copy()
        if 'vtmr' in futures_only.columns:
            futures_only = futures_only.sort_values('vtmr', ascending=False)
        
        spot_only = spot_df[~spot_df['ticker'].isin(merged['ticker'])].copy()
        
        if 'vtmr' in spot_only.columns:
            try:
                spot_only = spot_only.copy()
                spot_only.loc[:, 'flip_numeric'] = spot_only['vtmr'].astype(str).str.replace('x', '', case=False).astype(float)
                spot_only = spot_only[spot_only['flip_numeric'] >= 0.50]
                spot_only = spot_only.drop(columns=['flip_numeric'])
            except Exception as e:
                print(f"   Spot filtering error: {e}")
        
        if 'vtmr' in spot_only.columns:
            try:
                spot_only = spot_only.copy()
                spot_only.loc[:, 'sort_val'] = spot_only['vtmr'].astype(str).str.replace('x', '', case=False).astype(float)
                spot_only = spot_only.sort_values('sort_val', ascending=False).drop(columns=['sort_val'])
            except Exception:
                pass
        
        merged_cols = ['ticker', 'market_cap_spot', 'volume_spot', 'vtmr_spot', 'volume_fut', 'vtmr_display', 'oiss', 'funding']
        futures_cols = ['ticker', 'market_cap', 'volume', 'vtmr_display', 'oiss', 'funding']
        spot_cols = ['ticker', 'market_cap', 'volume', 'vtmr']
        
        html_content = ""
        html_content += DataProcessor._generate_table_html("Tokens in Both Futures & Spot Markets", merged, ORIGINAL_MATCHED_HEADERS, merged_cols)
        html_content += DataProcessor._generate_table_html("Remaining Futures-Only Tokens", futures_only, ORIGINAL_FUTURES_HEADERS, futures_cols)
        html_content += DataProcessor._generate_table_html("Remaining Spot-Only Tokens", spot_only, ORIGINAL_SPOT_HEADERS, spot_cols)
        current_time = now_str("%d-%m-%Y %H:%M:%S")
        
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
      <li><strong> Futures VTMR &gt; Spot VTMR (The Casino):</strong> If Futures volume is huge (e.g., 8x) but Spot is low, the price is being driven by leverage and speculation. This is fragile—expect violent "wicks" and liquidation hunts.</li>
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
                <small>This analysis was generated by you using the <strong>QuantVAT</strong> by <strong>@heisbuba</strong>. It empowers your market research but does not replace your due diligence. Verify the data, back your own instincts, and trade entirely at your own risk.</small>
            </div>
        """
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Quantitative Crypto Volume-driven Data Analysis Report</title>
            <meta charset="UTF-8">
            <style>{ORIGINAL_HTML_STYLE}</style>
        </head>
        <body>
            <div class="header">
                <h1>Cross-Market Crypto Analysis Report</h1>
                <p>Using Both Spot & Futures Market Data</p>
                <p><small>Generated on: {current_time}</small></p>
            </div>
            {html_content}
              {cheat_sheet_pdf_footer}
            <div class="footer">
                <p>Generated by QuantVAT | By (@heisbuba)</p>
            </div>
        </body>
        </html>
        """
        return html

def crypto_analysis_v4(user_keys, user_id) -> None:
    """Main execution flow for Advanced Analysis."""
    print("   ADVANCED QUANT CRYPTO VOLUME ANALYSIS")
    print("   Scanning for Futures PDF and Spot CSV/HTML files")
    print("   " + "=" * 50)
    
    # Find Files
    spot_file, futures_file = FileScanner.find_files(user_id)
    if not spot_file or not futures_file:
        print("   Required files not found.")
        raise FileNotFoundError("   You Need CoinAlyze Futures PDF and Spot Market Data. Kindly Generate Spot Data And Upload Futures PDF First.")
    
    # Parse Files
    futures_df = PDFParser.extract(futures_file)
    spot_df = DataProcessor.load_spot(spot_file)
    
    html_content = DataProcessor.generate_html_report(futures_df, spot_df)
    if html_content:
        # Create PDF
        pdf_path = convert_html_to_pdf(html_content, user_id)
        
        print("   🧹 Cleaning up source files after analysis...")
        cleanup_after_analysis(spot_file, futures_file)
        
        if pdf_path:
            print(f"   PDF saved: {pdf_path}")
            print("   📊 Analysis completed! Source files cleaned up.")
        else:
            print("   PDF conversion failed! Check API Key")
    else:
        print("   No data to generate report")
    print("   Advanced Analysis completed!")