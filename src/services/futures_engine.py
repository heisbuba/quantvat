import re
import unicodedata
import pandas as pd
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import pymupdf as fitz
except Exception:
    try:
        import fitz  # older package name
    except Exception:
        fitz = None


@dataclass
class TokenData:
    ticker: str
    name: str
    market_cap: str
    volume: str
    vtmr: float
    oi_pct: Optional[float] = None
    funding_pct: Optional[float] = None


class PDFParser:
    """Extracts tabular data from Coinalyze PDFs (PC and mobile layouts)."""

    # Currency figures are always "n/a" or "$"-prefixed (e.g. "$200.6m") -
    # never a bare number - which distinguishes them from VTMR and page furniture.
    _CURRENCY_RE = re.compile(r'^[+-]?\$[\d,]+\.?\d*[kKmMbBtT]?$')

    _PERCENT_RE = re.compile(r'^[+-]?[\d,]+\.?\d*%$')
    _PERCENT_PLACEHOLDERS = {'n/a', '-', '\u2013', '\u2014'}

    # VTMR: bare number, optional k/m/b/t suffix, no "$" and no "%".
    _PLAIN_NUMBER_RE = re.compile(r'^\d*\.?\d+[kKmMbBtT]?$')

    IGNORE_KEYWORDS = {
        'page', 'coinalyze', 'contract', 'filter', 'column',
        'mkt cap', 'vol 24h', 'vtmr', 'coins', 'all contracts', 'custom metrics', 'watchlists',
        'open interest - funding rate - liquidations',
    }

    _SUFFIX_MULT = {'k': 1e3, 'm': 1e6, 'b': 1e9, 't': 1e12}

    # CJK Radicals (U+2E80-2EFF) and CJK Radicals Supplement (U+2F00-2FDF)
    _TICKER_KEEP = r'\w\u2e80-\u2eff\u2f00-\u2fdf'

    @classmethod
    def _to_number(cls, s: str) -> float:
        s = s.strip()
        suffix = s[-1].lower() if s and s[-1].lower() in cls._SUFFIX_MULT else None
        if suffix:
            return float(s[:-1]) * cls._SUFFIX_MULT[suffix]
        return float(s)

    @staticmethod
    def _to_float(pct_str: Optional[str]) -> Optional[float]:
        if not pct_str:
            return None
        cleaned = pct_str.replace("%", "").replace("$", "").strip()
        if cleaned in ("-", "\u2013", "\u2014", "") or cleaned.lower() == "n/a":
            return None
        try:
            return float(cleaned)
        except Exception:
            return None

    # --- Token classifiers ---

    @classmethod
    def _is_currency(cls, tok: str) -> bool:
        return tok.lower() == 'n/a' or bool(cls._CURRENCY_RE.match(tok))

    @classmethod
    def _is_percent_or_placeholder(cls, tok: str) -> bool:
        return tok.lower() in cls._PERCENT_PLACEHOLDERS or bool(cls._PERCENT_RE.match(tok))

    @classmethod
    def _is_vtmr(cls, tok: str) -> bool:
        return bool(cls._PLAIN_NUMBER_RE.match(tok))

    # --- Block grouping ---

    @classmethod
    def _group_into_blocks(cls, words) -> List[List[str]]:
        by_block: "dict[int, list]" = {}
        order: List[int] = []
        for w in words:
            x0, y0, x1, y1, text, bno, lno, wno = w
            if not text.strip():
                continue
            if bno not in by_block:
                by_block[bno] = []
                order.append(bno)
            by_block[bno].append((lno, wno, text))

        blocks = []
        for bno in order:
            items = sorted(by_block[bno], key=lambda t: (t[0], t[1]))
            blocks.append([t for _, _, t in items])
        return blocks

    # --- Trailing-financial-tail consumption -- #

    @classmethod
    def _consume_financial_tail(cls, toks: List[str]) -> Optional[dict]:
        # Consumes a trailing [mkt_cap, vol, oi%?, funding%?, vtmr] tail from the right.
        toks = toks[:]

        if not toks or not cls._is_vtmr(toks[-1]):
            return None
        vtmr_tok = toks.pop()

        percents_popped: List[str] = []
        while toks and cls._is_percent_or_placeholder(toks[-1]) and len(percents_popped) < 2:
            percents_popped.append(toks.pop())

        if len(percents_popped) == 2:
            fund_str, oi_str = percents_popped[0], percents_popped[1]
        elif len(percents_popped) == 1:
            oi_str, fund_str = percents_popped[0], None
        else:
            oi_str = fund_str = None

        if len(toks) < 2 or not cls._is_currency(toks[-1]) or not cls._is_currency(toks[-2]):
            return None
        vol_tok = toks.pop()
        mc_tok = toks.pop()

        return {
            'remaining': toks,
            'market_cap': mc_tok.replace('$', '').replace(',', ''),
            'volume': vol_tok.replace('$', '').replace(',', ''),
            'vtmr': cls._to_number(vtmr_tok),
            'oi_pct': cls._to_float(oi_str),
            'funding_pct': cls._to_float(fund_str),
        }

    @classmethod
    def _is_ignorable(cls, tokens: List[str]) -> bool:
        joined_lower = ' '.join(tokens).lower()
        if any(k in joined_lower for k in cls.IGNORE_KEYWORDS):
            return True

        single_letters = ''.join(t.lower() for t in tokens if len(t) == 1 and t.isalpha())
        return 'coinalyze' in single_letters

    @classmethod
    def _looks_like_ticker(cls, tok: str) -> bool:
        
        if not tok or len(tok) > 15:
            return False
        if not tok.isascii():
            return any(ch.isalnum() for ch in tok)  # reject bare symbols/icons
        if tok.isdigit():
            return True
        return bool(re.fullmatch(r'[A-Z0-9]{1,10}', tok))

    @classmethod
    def _try_full_row(cls, tokens: List[str]) -> Optional[TokenData]:
        """PC layout: name + ticker + all 5 financial fields in one block."""
        parsed = cls._consume_financial_tail(tokens)
        if parsed is None:
            return None
        remaining = parsed['remaining']
        if not remaining:
            return None
        ticker_tok = remaining[-1]
        if not cls._looks_like_ticker(ticker_tok):
            return None
        name = ' '.join(remaining[:-1]).strip()
        if not name or not ticker_tok:
            return None
        return TokenData(
            ticker=ticker_tok, name=name,
            market_cap=parsed['market_cap'], volume=parsed['volume'],
            vtmr=parsed['vtmr'], oi_pct=parsed['oi_pct'], funding_pct=parsed['funding_pct'],
        )

    @classmethod
    def _try_financial_only(cls, tokens: List[str]) -> Optional[dict]:
        """Mobile layout, right column: just the 5 financial fields."""
        parsed = cls._consume_financial_tail(tokens)
        if parsed is None or parsed['remaining']:
            return None
        return parsed

    @classmethod
    def _try_name_ticker_only(cls, tokens: List[str]) -> Optional[Tuple[str, str]]:
        """Mobile layout, left column: wrapped name lines + trailing ticker."""
        if len(tokens) < 2:
            return None
        if cls._is_vtmr(tokens[-1]) and cls._consume_financial_tail(tokens) is not None:
            return None  # financial-shaped block must never land here
        ticker_tok = tokens[-1]
        if not cls._looks_like_ticker(ticker_tok):
            return None
        name = ' '.join(tokens[:-1]).strip()
        if not name or not ticker_tok:
            return None
        return name, ticker_tok

    # --- Core extraction logic ---

    @classmethod
    def _parse_page(cls, blocks: List[List[str]]) -> List[TokenData]:
        full_rows: List[TokenData] = []
        financial_entries: List[dict] = []
        name_entries: List[Tuple[str, str]] = []

        for tokens in blocks:
            if cls._is_ignorable(tokens):
                continue

            full_row = cls._try_full_row(tokens)
            if full_row is not None:
                full_rows.append(full_row)
                continue

            fin = cls._try_financial_only(tokens)
            if fin is not None:
                financial_entries.append(fin)
                continue

            name_pair = cls._try_name_ticker_only(tokens)
            if name_pair is not None:
                name_entries.append(name_pair)
                continue
            # else: page furniture / unrecognized block - skip silently

        if financial_entries or name_entries:
            if len(financial_entries) != len(name_entries):
                print(
                    f"   WARNING: page has {len(name_entries)} name/ticker blocks but "
                    f"{len(financial_entries)} financial blocks - mismatch, positional "
                    f"pairing below is likely misaligned and data may be silently dropped/mixed up"
                )
            limit = min(len(financial_entries), len(name_entries))
            for k in range(limit):
                name, ticker = name_entries[k]
                fin = financial_entries[k]
                full_rows.append(TokenData(
                    ticker=ticker, name=name,
                    market_cap=fin['market_cap'], volume=fin['volume'],
                    vtmr=fin['vtmr'], oi_pct=fin['oi_pct'], funding_pct=fin['funding_pct'],
                ))

        return full_rows

    @classmethod
    def extract(cls, path) -> pd.DataFrame:
        print(f"   Parsing Futures PDF: {path.name}")
        if fitz is None:
            print("   pymupdf not available - PDF parsing disabled.")
            return pd.DataFrame()

        data: List[TokenData] = []
        try:
            doc = fitz.open(path)
            for page in doc:
                words = page.get_text("words")
                blocks = cls._group_into_blocks(words)
                page_tokens = cls._parse_page(blocks)
                data.extend(page_tokens)
                print(f"   Page parsed: {len(page_tokens)} rows")

            print(f"   Extracted {len(data)} futures tokens")
            if not data:
                return pd.DataFrame()
            df = pd.DataFrame([vars(t) for t in data])

            # Preserve Unicode tickers (CJK, etc.)
            df['ticker'] = df['ticker'].apply(
                lambda x: re.sub(rf'[^{cls._TICKER_KEEP}]', '', str(x))
            )
            df['ticker'] = df['ticker'].apply(lambda x: unicodedata.normalize('NFKC', str(x)))
            df['name'] = df['name'].apply(lambda x: unicodedata.normalize('NFKC', str(x)))

            df = df[df['ticker'].str.len() >= 1]
            print(f"   Valid futures tokens: {len(df)}")
            return df
        except Exception as e:
            print(f"   PDF Error: {e}")
            return pd.DataFrame()

    @classmethod
    def extract_and_persist(cls, pdf_path: Path) -> Optional[Path]:
        df = cls.extract(pdf_path)
        if df.empty:
            return None
        csv_path = pdf_path.with_suffix(".csv")
        try:
            # na_rep='n/a'
            df.to_csv(csv_path, index=False, na_rep='n/a')
        except Exception as e:
            print(f"   Could not parse futures CSV: {e}")
            return None
        print(f"   Futures data parsed: {csv_path.name}")
        return csv_path
