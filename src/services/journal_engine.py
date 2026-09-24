import json
import io
import os
import re
import uuid
import datetime
from PIL import Image
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from ..config import get_user_keys, update_user_keys, firestore

# create Trading Journal in user's drive
SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
]

FOLDER_MIME = 'application/vnd.google-apps.folder'


class JournalEngine:
    ROOT_FOLDER_NAME = "Trading Journal"
    MODE_FOLDER_NAMES = {"normal": "Spot", "meme": "Meme"}
    VALID_MODES = ("normal", "meme")
    IMAGE_SLOTS = ("before", "after")
    MAX_IMAGE_DIMENSION = 1600  # longest side, px

    # --- Auth ---
    @staticmethod
    def get_flow():
        redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "")
        if redirect_uri.startswith("http://"):
            os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
        else:
            os.environ.pop('OAUTHLIB_INSECURE_TRANSPORT', None)
        os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'
        return Flow.from_client_config(
            client_config={
                "web": {
                    "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
                    "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=SCOPES,
            redirect_uri=os.environ.get("GOOGLE_REDIRECT_URI")
        )

    @staticmethod
    def get_creds(uid, user_data=None):
        if user_data is None:
            user_data = get_user_keys(uid)
        token_json = user_data.get("google_token_json")
        if not token_json:
            return None
        try:
            creds = Credentials.from_authorized_user_info(json.loads(token_json))
        except Exception as e:
            print(f"Token Load Error: {e}")
            return None

        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleAuthRequest())
                update_user_keys(uid, {"google_token_json": creds.to_json()})
            except Exception as e:
                print(f"Token Refresh Error: {e}")
                return None

        return creds

    @staticmethod
    def has_drive_file_scope(creds) -> bool:
        if not creds:
            return False
        scopes = set(getattr(creds, 'scopes', None) or [])
        return 'https://www.googleapis.com/auth/drive.file' in scopes

    @staticmethod
    def get_drive_service(creds):
        return build('drive', 'v3', credentials=creds)

    @staticmethod
    def load_journal(service, file_id):
        try:
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
            content = fh.getvalue().decode('utf-8')
            return json.loads(content) if content else []
        except HttpError as e:
            if e.resp.status == 404:
                raise
            print(f"Journal Load Error: {e}")
            return []
        except Exception as e:
            print(f"Journal Load Error: {e}")
            return []

    @staticmethod
    def save_to_drive(service, file_id, journal_data):
        media = MediaIoBaseUpload(
            io.BytesIO(json.dumps(journal_data).encode('utf-8')),
            mimetype='application/json',
            resumable=True
        )
        service.files().update(fileId=file_id, media_body=media).execute()

    @staticmethod
    def _find_or_create_folder(service, name, parent_id):
        safe_name = name.replace("'", "\\'")
        q = (f"name='{safe_name}' and mimeType='{FOLDER_MIME}' "
             f"and '{parent_id}' in parents and trashed=false")
        resp = service.files().list(
            q=q, spaces='drive', fields='files(id, name)', pageSize=1
        ).execute()
        files = resp.get('files', [])
        if files:
            return files[0]['id']

        meta = {'name': name, 'mimeType': FOLDER_MIME, 'parents': [parent_id]}
        folder = service.files().create(body=meta, fields='id').execute()
        return folder.get('id')

    @classmethod
    def get_root_folder_id(cls, service, uid, user_data=None):
        if user_data is None:
            user_data = get_user_keys(uid)
        cached = user_data.get("journal_root_folder_id")
        if cached:
            return cached

        parent_id = user_data.get("journal_parent_folder_id") or 'root'
        folder_id = cls._find_or_create_folder(service, cls.ROOT_FOLDER_NAME, parent_id)
        update_user_keys(uid, {"journal_root_folder_id": folder_id})
        user_data["journal_root_folder_id"] = folder_id
        return folder_id

    @classmethod
    def get_mode_folder_id(cls, service, uid, mode, user_data=None):
        if mode not in cls.VALID_MODES:
            raise ValueError(f"Invalid journal mode: {mode}")
        if user_data is None:
            user_data = get_user_keys(uid)

        cache_key = f"journal_{mode}_folder_id"
        cached = user_data.get(cache_key)
        if cached:
            return cached

        root_id = cls.get_root_folder_id(service, uid, user_data=user_data)
        folder_id = cls._find_or_create_folder(service, cls.MODE_FOLDER_NAMES[mode], root_id)
        update_user_keys(uid, {cache_key: folder_id})
        user_data[cache_key] = folder_id
        return folder_id

    @classmethod
    def get_charts_folder_id(cls, service, uid, mode, month_str, user_data=None):
        if user_data is None:
            user_data = get_user_keys(uid)

        cache_key = f"journal_{mode}_charts_{month_str}_folder_id"
        cached = user_data.get(cache_key)
        if cached:
            return cached

        mode_folder_id = cls.get_mode_folder_id(service, uid, mode, user_data=user_data)
        charts_root_id = cls._find_or_create_folder(service, "charts", mode_folder_id)
        month_folder_id = cls._find_or_create_folder(service, month_str, charts_root_id)
        update_user_keys(uid, {cache_key: month_folder_id})
        user_data[cache_key] = month_folder_id
        return month_folder_id

    SHARD_PREFIX = "trades-"
    SHARD_SUFFIX = ".json"

    @staticmethod
    def _trade_month(trade):
        m = trade.get('month')
        if m:
            return m
        d = trade.get('trade_date')
        if d:
            try:
                return datetime.datetime.strptime(d, "%Y-%m-%d").strftime("%Y-%m")
            except (ValueError, TypeError):
                pass
        return "undated"

    @classmethod
    def _month_shard_key(cls, mode, month):
        return f"journal_{mode}_m_{month}_file_id"

    @staticmethod
    def _list_shard_files(service, folder_id):
        q = (f"name contains '{JournalEngine.SHARD_PREFIX}' and "
             f"'{folder_id}' in parents and trashed=false")
        resp = service.files().list(
            q=q, spaces='drive', fields='files(id, name)', pageSize=200
        ).execute()
        shards = {}
        for f in resp.get('files', []):
            name = f['name']
            if not (name.startswith(JournalEngine.SHARD_PREFIX) and name.endswith(JournalEngine.SHARD_SUFFIX)):
                continue
            month = name[len(JournalEngine.SHARD_PREFIX):-len(JournalEngine.SHARD_SUFFIX)]
            if month:
                shards[month] = f['id']
        return dict(sorted(shards.items(), reverse=True))

    @classmethod
    def _get_shard_file_id(cls, service, uid, mode, month, mode_folder_id, user_data):
        key = cls._month_shard_key(mode, month)
        cached = user_data.get(key)
        if cached:
            return cached
        shards = cls._list_shard_files(service, mode_folder_id)
        if month in shards:
            file_id = shards[month]
        else:
            media = MediaIoBaseUpload(
                io.BytesIO(json.dumps([]).encode('utf-8')),
                mimetype='application/json', resumable=True
            )
            f = service.files().create(
                body={'name': f"{cls.SHARD_PREFIX}{month}{cls.SHARD_SUFFIX}",
                      'parents': [mode_folder_id]},
                media_body=media, fields='id'
            ).execute()
            file_id = f['id']
        update_user_keys(uid, {key: file_id})
        user_data[key] = file_id
        return file_id

    @classmethod
    def _reset_month_cache(cls, uid, mode, month, user_data):
        key = cls._month_shard_key(mode, month)
        update_user_keys(uid, {key: firestore.DELETE_FIELD})
        if user_data is not None:
            user_data.pop(key, None)

    @classmethod
    def load_mode_journal(cls, service, uid, mode, user_data=None, creds=None):
        if user_data is None:
            user_data = get_user_keys(uid)
        mode_folder_id = cls.get_mode_folder_id(service, uid, mode, user_data=user_data)
        shards = cls._list_shard_files(service, mode_folder_id)
        if not shards:
            try:
                q = (f"name='trades.json' and '{mode_folder_id}' in parents "
                     f"and trashed=false")
                leftover = service.files().list(
                    q=q, spaces='drive', fields='files(id, name)', pageSize=1
                ).execute()
                if leftover.get('files'):
                    from ..config import log_error
                    log_error(
                        source="journal_engine",
                        message=f"Legacy trades.json with no shards for mode={mode} — "
                                f"data present but unread (migration removed).",
                        uid=uid,
                    )
            except Exception:
                pass  # detection must never break the load path
        def _fetch_one(month, file_id):
            worker_service = (
                cls.get_drive_service(creds) if creds is not None else service
            )
            try:
                return cls.load_journal(worker_service, file_id)
            except HttpError as e:
                if e.resp.status != 404:
                    raise
                cls._reset_month_cache(uid, mode, month, user_data)
                fresh = cls._list_shard_files(worker_service, mode_folder_id)
                if month in fresh:
                    return cls.load_journal(worker_service, fresh[month])
                return []

        trades = []
        if len(shards) > 1 and creds is not None:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="journal_shards") as pool:
                for part in pool.map(lambda kv: _fetch_one(*kv), shards.items()):
                    trades.extend(part)
        else:
            for month, file_id in shards.items():
                trades.extend(_fetch_one(month, file_id))
        return trades

    @classmethod
    def save_trade_v2(cls, service, uid, mode, trade_data, user_data=None):
        if user_data is None:
            user_data = get_user_keys(uid)

        trade_data['mode'] = mode
        if 'trade_date' in trade_data:
            try:
                dt = datetime.datetime.strptime(trade_data['trade_date'], "%Y-%m-%d")
                trade_data['week'] = dt.strftime("%Y-W%W")
                trade_data['month'] = dt.strftime("%Y-%m")
            except ValueError:
                pass

        month = cls._trade_month(trade_data)
        mode_folder_id = cls.get_mode_folder_id(service, uid, mode, user_data=user_data)

        file_id = cls._get_shard_file_id(service, uid, mode, month, mode_folder_id, user_data)
        try:
            journal = cls.load_journal(service, file_id)
        except HttpError as e:
            if e.resp.status != 404:
                raise
            # Stale cached shard id — same recovery load_mode_journal uses.
            cls._reset_month_cache(uid, mode, month, user_data)
            file_id = cls._get_shard_file_id(service, uid, mode, month, mode_folder_id, user_data)
            journal = cls.load_journal(service, file_id)

        trade_id = trade_data.get('id')
        updated = False
        if not trade_id:
            trade_data['id'] = str(uuid.uuid4())
            journal.append(trade_data)
        else:
            for i, existing in enumerate(journal):
                if existing.get('id') == trade_id:
                    journal[i] = trade_data
                    updated = True
                    break
            if not updated:
                for other_month, other_id in cls._list_shard_files(service, mode_folder_id).items():
                    if other_month == month:
                        continue
                    try:
                        other = cls.load_journal(service, other_id)
                    except HttpError:
                        continue
                    kept = [t for t in other if str(t.get('id')) != str(trade_id)]
                    if len(kept) != len(other):
                        cls.save_to_drive(service, other_id, kept)
                        break
                journal.append(trade_data)

        cls.save_to_drive(service, file_id, journal)
        return True

    @classmethod
    def get_trade_by_id(cls, service, uid, mode, trade_id, user_data=None):
        if user_data is None:
            user_data = get_user_keys(uid)
        mode_folder_id = cls.get_mode_folder_id(service, uid, mode, user_data=user_data)
        for _month, file_id in cls._list_shard_files(service, mode_folder_id).items():
            try:
                journal = cls.load_journal(service, file_id)
            except HttpError as e:
                if e.resp.status != 404:
                    raise
                continue
            for t in journal:
                if str(t.get('id')) == str(trade_id):
                    return t
        return None

    @classmethod
    def delete_trade_v2(cls, service, uid, mode, trade_id, user_data=None):
        if user_data is None:
            user_data = get_user_keys(uid)
        mode_folder_id = cls.get_mode_folder_id(service, uid, mode, user_data=user_data)
        for _month, file_id in cls._list_shard_files(service, mode_folder_id).items():
            try:
                journal = cls.load_journal(service, file_id)
            except HttpError as e:
                if e.resp.status != 404:
                    raise
                continue
            kept = [t for t in journal if str(t.get('id')) != str(trade_id)]
            if len(kept) < len(journal):
                cls.save_to_drive(service, file_id, kept)
                return True
        return False

    @classmethod
    def _compress_image(cls, file_stream):
        img = Image.open(file_stream)
        img.load()

        if img.mode == "P":
            img = img.convert("RGBA" if "transparency" in img.info else "RGB")
        elif img.mode == "CMYK":
            img = img.convert("RGB")
        elif img.mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGB")

        w, h = img.size
        if max(w, h) > cls.MAX_IMAGE_DIMENSION:
            scale = cls.MAX_IMAGE_DIMENSION / float(max(w, h))
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

        out = io.BytesIO()
        img.save(out, format="WEBP", lossless=True, method=6)
        out.seek(0)
        return out

    @staticmethod
    def _sanitize_filename_part(value, max_len=20):
        cleaned = re.sub(r'[^A-Za-z0-9]+', '', value or '')
        return cleaned.upper()[:max_len] or "TRADE"

    @classmethod
    def _resolve_unique_filename(cls, service, folder_id, trade, slot):
        ticker = cls._sanitize_filename_part(trade.get('ticker'))
        date_str = trade.get('trade_date') or 'nodate'
        full_id = str(trade.get('id', '')).replace('-', '').upper() or "0" * 32

        for length in range(5, len(full_id) + 1):
            short_id = full_id[:length]
            filename = f"{short_id}-{ticker}-{date_str}-{slot.capitalize()}.webp"
            safe_name = filename.replace("\\", "\\\\").replace("'", "\\'")
            query = f"name = '{safe_name}' and '{folder_id}' in parents and trashed = false"
            existing = service.files().list(q=query, fields='files(id)', pageSize=1).execute()
            if not existing.get('files'):
                return filename

        return f"{full_id}-{ticker}-{date_str}-{slot.capitalize()}.webp"

    @classmethod
    def upload_chart_image(cls, service, uid, mode, trade, slot, file_stream, user_data=None):
        if slot not in cls.IMAGE_SLOTS:
            raise ValueError(f"Invalid image slot: {slot}")
        if user_data is None:
            user_data = get_user_keys(uid)

        month_str = trade.get('month')
        if not month_str:
            month_str = datetime.datetime.strptime(
                trade['trade_date'], "%Y-%m-%d"
            ).strftime("%Y-%m")

        folder_id = cls.get_charts_folder_id(service, uid, mode, month_str, user_data=user_data)

        old_id = trade.get(f"{slot}_image_id")
        if old_id:
            cls.delete_chart_image(service, old_id)

        compressed = cls._compress_image(file_stream)
        filename = cls._resolve_unique_filename(service, folder_id, trade, slot)
        meta = {'name': filename, 'parents': [folder_id]}
        media = MediaIoBaseUpload(compressed, mimetype='image/webp', resumable=True)
        file = service.files().create(body=meta, media_body=media, fields='id').execute()
        return file.get('id')

    @staticmethod
    def delete_chart_image(service, file_id):
        try:
            service.files().delete(fileId=file_id).execute()
            return True
        except HttpError as e:
            if e.resp.status == 404:
                return True
            print(f"Chart image delete error: {e}")
            return False
        except Exception as e:
            print(f"Chart image delete error: {e}")
            return False

    @staticmethod
    def parse_pnl(pnl_str):
        try:
            clean = re.sub(r'[^\d\.-]', '', str(pnl_str))
            return float(clean) if clean else 0.0
        except Exception:
            return 0.0

    @classmethod
    def calculate_stats(cls, journal_data):
        if not journal_data:
            return {"winrate": "0%", "best_trade": "--", "bias": "Neutral"}

        wins = [t for t in journal_data if cls.parse_pnl(t.get('pnl', 0)) > 0]
        total = len(journal_data)
        winrate = (len(wins) / total) * 100 if total > 0 else 0

        best_trade = max(journal_data, key=lambda x: cls.parse_pnl(x.get('pnl', 0)), default={})

        biases = []
        for t in journal_data:
            if t.get('bias'):
                biases.append(t.get('bias'))
            elif 'rules_followed' in t:
                biases.append("Disciplined" if str(t['rules_followed']) == "true" else "Mistake")

        main_bias = max(set(biases), key=biases.count) if biases else "Neutral"

        return {
            "winrate": f"{winrate:.0f}%",
            "best_trade": best_trade.get('ticker', '--'),
            "bias": main_bias
        }