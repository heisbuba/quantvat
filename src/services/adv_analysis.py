import re
import datetime
import html as html_lib
import pandas as pd
from typing import List, Optional, Tuple
from pathlib import Path

# Import our modular components
from ..state import get_user_temp_dir, update_progress, get_pending_files, clear_pending_file
from .utils import now_str, cleanup_after_analysis

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
    .ticker-btn { display: block; width: 100%; height: 100%; color: #10b981; text-decoration: none; font-weight: 800; box-sizing: border-box; touch-action: manipulation; -webkit-tap-highlight-color: rgba(16, 185, 129, 0.15); transition: background 0.15s; }
    .ticker-btn:active { background: rgba(16, 185, 129, 0.1); }
    
"""

ORIGINAL_MATCHED_HEADERS = ["Ticker", "Spot MrktCap", "Spot Volume", "Spot VTMR", "Futures Volume", "Futures VTMR", "OISS", "Funding Rate"]
ORIGINAL_FUTURES_HEADERS = ["Ticker", "Market Cap", "Volume", "VTMR", "OISS", "Funding Rate"]
ORIGINAL_SPOT_HEADERS = ["Ticker", "MarketCap", "Volume", "VTMR"]


class SignalEngine:

    @staticmethod
    def _oi_score_and_signal(oi_change: float) -> Tuple[int, str]:
        if oi_change > 0.20: return 5, "Strong"
        if oi_change > 0.10: return 4, "Bullish"
        if oi_change > 0.00: return 3, "Build-Up"
        if oi_change > -0.10: return 2, "Weakening"
        if oi_change > -0.20: return 1, "Exiting"
        return 0, "Exiting"

    @staticmethod
    def _funding_score_and_signal(funding_val: float) -> Tuple[str, str]:
        if funding_val >= 0.05: return "Greed", "oi-strong"
        if funding_val > 0.00: return "Bullish", "oi-strong"
        if funding_val <= -0.05: return "Extreme Fear", "oi-weak"
        if funding_val < 0.00: return "Bearish", "oi-weak"
        return "Neutral", ""

    @classmethod
    def make_oiss(cls, oi_pct: Optional[float]) -> str:
        if oi_pct is None or (isinstance(oi_pct, float) and pd.isna(oi_pct)):
            return "-"
        try:
            oi_change = oi_pct / 100
            score, signal = cls._oi_score_and_signal(oi_change)

            if oi_change > 0: css_class = "oi-strong"
            elif oi_change < 0: css_class = "oi-weak"
            else: css_class = ""

            sign = "+" if oi_change > 0 else ""
            if css_class:
                return f'<span class="{css_class}">{sign}{oi_change*100:.0f}%</span> {signal}'
            return f"{sign}{oi_change*100:.0f}% {signal}"
        except Exception:
            return "-"

    @classmethod
    def make_funding_signal(cls, funding_pct: Optional[float]) -> str:
        if funding_pct is None or (isinstance(funding_pct, float) and pd.isna(funding_pct)):
            return "-"
        try:
            val = float(funding_pct)
            signal_word, css_class = cls._funding_score_and_signal(val)

            if css_class:
                return f'<span class="{css_class}">{val}%</span> <span style="font-size:0.8em; color:#7f8c8d;">{signal_word}</span>'
            return f'{val}% {signal_word}'
        except Exception:
            return "-"


class DataProcessor:
    """Handles Dataframe loading, merging, and HTML generation."""
    
    _CAP_SUFFIX_MULT = {'k': 1e3, 'm': 1e6, 'b': 1e9, 't': 1e12}

    @staticmethod
    def format_ticker_btn(sym_val) -> str:
        """Helper method isolated to avoid scope issues."""
        sym_clean = html_lib.escape(str(sym_val).strip().upper(), quote=True)
        return f'<a href="/deep-diver?ticker={sym_clean}" class="ticker-btn">{sym_clean}</a>'

    @staticmethod
    def _parse_cap(value) -> Optional[float]:
        """Parses a market cap string like '$67.2k', '21.57M', 'n/a' into a raw USD float."""
        if value is None:
            return None
        s = str(value).strip().replace('$', '').replace(',', '')
        if s == '' or s.lower() == 'n/a':
            return None
        suffix = s[-1].lower() if s and s[-1].lower() in DataProcessor._CAP_SUFFIX_MULT else None
        try:
            if suffix:
                return float(s[:-1]) * DataProcessor._CAP_SUFFIX_MULT[suffix]
            return float(s)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _caps_compatible(spot_cap, fut_cap, max_ratio: float = 5.0) -> bool:
        """Guards against cross-market ticker collisions."""
        spot_val = DataProcessor._parse_cap(spot_cap)
        fut_val = DataProcessor._parse_cap(fut_cap)
        if spot_val is None or fut_val is None or spot_val <= 0 or fut_val <= 0:
            return True
        ratio = max(spot_val, fut_val) / min(spot_val, fut_val)
        return ratio <= max_ratio

    @staticmethod
    def load_spot(path: Path) -> pd.DataFrame:
        print(f"   Parsing Spot File: {path.name}")
        try:
            if path.suffix == '.html':
                df = pd.read_html(str(path), encoding='utf-8')[0]
            else:
                df = pd.read_csv(path, encoding='utf-8')
            df.columns = [c.lower().replace(' ', '_') for c in df.columns]
            
            col_map = {
                'ticker': 'ticker',
                'symbol': 'ticker', 
                'vtmr': 'vtmr',         
                'spot_vtmr': 'vtmr', 
                'flipping_multiple': 'vtmr',
                'market_cap': 'market_cap',
                'marketcap': 'market_cap',
                'volume_24h': 'volume',
                'volume': 'volume'
            }
            
            df = df.rename(columns=col_map, errors='ignore')
            
            if 'ticker' not in df.columns:
                for col in df.columns:
                    if 'sym' in col or 'tick' in col or 'tok' in col:
                        df = df.rename(columns={col: 'ticker'})
                        break

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
        
        SAFE_HTML_HEADERS = {"Ticker", "OISS", "Funding Rate"}
        for col in df_display.columns:
            if col not in SAFE_HTML_HEADERS:
                df_display[col] = df_display[col].apply(lambda v: html_lib.escape(str(v)))

        table_html = df_display.to_html(index=False, classes='table', escape=False)
        return f'<div class="table-container"><h2>{title}</h2>{table_html}</div>'

    @staticmethod
    def generate_html_report(futures_df: pd.DataFrame, spot_df: pd.DataFrame) -> Optional[str]:
        """Merges Spot and Futures dataframes and creates the final HTML report."""
        if futures_df.empty or spot_df.empty:
            return None

        futures_df = futures_df.copy()
        if 'oi_pct' in futures_df.columns:
            futures_df['oiss'] = futures_df['oi_pct'].apply(SignalEngine.make_oiss)
        else:
            futures_df['oiss'] = "-"

        if 'funding_pct' in futures_df.columns:
            futures_df['funding'] = futures_df['funding_pct'].apply(SignalEngine.make_funding_signal)
        else:
            futures_df['funding'] = "-"

        valid_futures = futures_df.copy()
        try:
            if 'vtmr' in valid_futures.columns:
                valid_futures['vtmr_display'] = valid_futures['vtmr'].apply(lambda x: f"{x:.2f}x")
        except Exception as e:
            print(f"   Futures display formatting error: {e}")
            valid_futures['vtmr_display'] = valid_futures['vtmr']

        merged = pd.merge(spot_df, valid_futures, on='ticker', how='inner', suffixes=('_spot', '_fut'))

        if not merged.empty and 'market_cap_spot' in merged.columns and 'market_cap_fut' in merged.columns:
            cap_ok = merged.apply(
                lambda r: DataProcessor._caps_compatible(r.get('market_cap_spot'), r.get('market_cap_fut')),
                axis=1
            )
            mismatched = merged[~cap_ok]
            if not mismatched.empty:
                print(f"   ⚠️  Excluded {len(mismatched)} ticker collision(s) from cross-market merge "
                      f"(market cap mismatch, likely different tokens sharing a symbol): "
                      f"{sorted(mismatched['ticker'].unique().tolist())}")
            merged = merged[cap_ok].copy()

        matched_tickers = set(merged['ticker'])

        if 'vtmr_fut' in merged.columns:
            merged = merged.sort_values('vtmr_fut', ascending=False)

        futures_only = valid_futures[~valid_futures['ticker'].isin(matched_tickers)].copy()
        if 'vtmr' in futures_only.columns:
            futures_only = futures_only.sort_values('vtmr', ascending=False)

        spot_only = spot_df[~spot_df['ticker'].isin(matched_tickers)].copy()
        
        if 'vtmr' in spot_only.columns:
            try:
                spot_only = spot_only.copy()
                spot_only.loc[:, 'sort_val'] = spot_only['vtmr'].astype(str).str.replace('x', '', case=False).astype(float)
                spot_only = spot_only.sort_values('sort_val', ascending=False).drop(columns=['sort_val'])
            except Exception as e:
                print(f"   Spot filtering error: {e}")

        # Map button formatting via isolated staticmethod
        if 'ticker' in merged.columns:
            merged['ticker'] = merged['ticker'].apply(DataProcessor.format_ticker_btn)
        if 'ticker' in futures_only.columns:
            futures_only['ticker'] = futures_only['ticker'].apply(DataProcessor.format_ticker_btn)
        if 'ticker' in spot_only.columns:
            spot_only['ticker'] = spot_only['ticker'].apply(DataProcessor.format_ticker_btn)
        
        merged_cols = ['ticker', 'market_cap_spot', 'volume_spot', 'vtmr_spot', 'volume_fut', 'vtmr_display', 'oiss', 'funding']
        futures_cols = ['ticker', 'market_cap', 'volume', 'vtmr_display', 'oiss', 'funding']
        spot_cols = ['ticker', 'market_cap', 'volume', 'vtmr']
        
        tables_body = ""
        tables_body += DataProcessor._generate_table_html("Tokens in Both Futures & Spot Markets", merged, ORIGINAL_MATCHED_HEADERS, merged_cols)
        tables_body += DataProcessor._generate_table_html("Remaining Futures-Only Tokens", futures_only, ORIGINAL_FUTURES_HEADERS, futures_cols)
        tables_body += DataProcessor._generate_table_html("Remaining Spot-Only Tokens", spot_only, ORIGINAL_SPOT_HEADERS, spot_cols)
        current_time = now_str("%d-%m-%Y %H:%M:%S")
        
        cheat_sheet_footer = """
            <div style="margin-top: 30px; padding: 15px; background: #ecf0f1; border-radius: 8px;">
    <h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 5px; margin-top: 0;">Guide to Reading the Cross‑Market Report</h2>
    <p>For a comprehensive guide on how to interpret the cross‑market report, please visit: <a href="https://www.quantvat.name.ng/2026/08/reading-quantvat-cross-market-report.html" target="_blank">here</a></p>
</div>
        """
        
        final_document = f"""
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
                <p><small>Generated on: {current_time}</small></p>
            </div>
            {tables_body}
            {cheat_sheet_footer}
            <div class="footer">
                <p>Generated by <a href="/" style="color:#10b981; text-decoration:none; font-weight:bold;">QuantVAT</a> | By (@heisbuba) | Not Financial Advice. Just Data!</p>
            </div>
        </body>
        </html>
        """
        return final_document

def adv_analysis_v5(user_keys, user_id) -> None:
    """Main execution flow for Advanced Analysis."""
    print("   ADVANCED CROSS-MARKET ANALYSIS")
    print("   Scanning for Futures CSV and Spot Analysis files")
    print("   " + "=" * 40)
    update_progress(user_id, 10, "Locating Spot and Futures files...", "active")
    
    pending = get_pending_files(user_id)
    spot_file = pending.get("spot")
    futures_file = pending.get("futures")
    if not spot_file or not futures_file:
        print("   Required files not found.")
        raise FileNotFoundError("   You Need CoinAlyze Futures PDF and Spot Market Data. Kindly Generate Spot Data And Upload Futures PDF First.")
    
    update_progress(user_id, 40, "Loading Futures and Spot data...", "active")
    spot_df = DataProcessor.load_spot(spot_file)
    try:
        futures_df = pd.read_csv(futures_file, dtype={'ticker': str}, encoding='utf-8')
        print(f"   Futures CSV Retrieved: {futures_file.name}")
    except Exception as e:
        print(f"   Futures CSV Error: {e}")
        futures_df = pd.DataFrame()

    def safe_float(val, default):
        try:
            if val is None or str(val).strip() == "":
                return default
            return float(val)
        except (ValueError, TypeError):
            return default

    MC_SUFFIX_MULT = {'k': 1e3, 'm': 1e6, 'b': 1e9, 't': 1e12}

    def parse_mc(val, default):
        """Parses market-cap shorthand like '5m', '1.2b', '500k', '1t' into a raw float."""
        if val is None:
            return default
        s = str(val).strip().replace('$', '').replace(',', '')
        if s == "":
            return default
        suffix = s[-1].lower() if s and s[-1].lower() in MC_SUFFIX_MULT else None
        try:
            if suffix:
                return float(s[:-1]) * MC_SUFFIX_MULT[suffix]
            return float(s)
        except (ValueError, TypeError):
            return default

    settings = user_keys.get("engine_settings", {}) if user_keys else {}
    MIN_F_VTMR = safe_float(settings.get('min_f_vtmr'), 0.5)
    MAX_F_VTMR = safe_float(settings.get('max_f_vtmr'), 399.0)
    MIN_F_MC = parse_mc(settings.get('min_f_mc'), 0.0)
    MAX_F_MC = parse_mc(settings.get('max_f_mc'), float('inf'))

    if not futures_df.empty and 'vtmr' in futures_df.columns:
        before_count = len(futures_df)
        futures_df = futures_df[(futures_df['vtmr'] >= MIN_F_VTMR) & (futures_df['vtmr'] <= MAX_F_VTMR)]
        print(f" Extracted {len(futures_df)} tokens out of {before_count} with VTMR filter applied")
        print("   " + "=" * 40)

    if not futures_df.empty and 'market_cap' in futures_df.columns:
        before_count = len(futures_df)
        mc_numeric = futures_df['market_cap'].apply(DataProcessor._parse_cap)
        # Rows with missing/unparseable market cap pass through unfiltered (VTMR filter already vetted them)
        in_range = mc_numeric.isna() | ((mc_numeric >= MIN_F_MC) & (mc_numeric <= MAX_F_MC))
        futures_df = futures_df[in_range]
        print(f" Extracted {len(futures_df)} tokens out of {before_count} with Market Cap filter applied")
        print("   " + "=" * 40)
    
    update_progress(user_id, 65, "Merging cross-market signals...", "active")
    html_content = DataProcessor.generate_html_report(futures_df, spot_df)
    if not html_content:
        print("   No data to generate report")
        raise ValueError("No matching data between spot and futures files — check both sources.")

    update_progress(user_id, 85, "Saving report...", "active")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    html_file = get_user_temp_dir(user_id) / f"cross-market-analysis-{timestamp}.html"
    try:
        html_file.write_text(html_content, encoding="utf-8")
    except Exception as e:
        print(f"   Report save failed: {e}")
        raise RuntimeError(f"Could not save the report: {e}")
    print(f"   Report saved: {html_file}")
    print("   🧹 Cleaning up source files (kept spot file).")
    cleanup_after_analysis(spot_file, futures_file, keep_spot=True)
    clear_pending_file(user_id, "futures")
    print("   📊  Analysis completed!")