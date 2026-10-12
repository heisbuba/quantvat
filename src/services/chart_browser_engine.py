import json
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from PIL import Image, ImageOps

from ..config import update_user_keys, get_user_keys, firestore
from ..state import get_user_temp_dir
from .journal_engine import FOLDER_MIME

BROWSER_SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
FULL_DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive'

TOKEN_FIELD = "google_browser_token_json"
LAST_FOLDER_FIELD = "chart_browser_last_folder"

CACHE_SUBDIR = "chart_browser"
INDEX_NAME = "index.json"

IMAGE_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/avif": ".avif",
}

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

MAX_IMAGES = 1000
MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_CACHE_BYTES = 300 * 1024 * 1024
CACHE_TTL_SECONDS = 48 * 60 * 60
DOWNLOAD_CHUNK = 4 * 1024 * 1024

PREVIEW_MAX_PX = 1600
PREVIEW_QUALITY = 78
PREVIEW_MIN_BYTES = 250 * 1024   # smaller originals are served as-is
PREVIEW_TAG = "~p"               # '~' can never appear in a Drive id

# Drive's orderBy for each sort. Both date sorts fetch newest-first so that,
# in a folder over MAX_IMAGES, the cut-off drops the oldest files and never
# the latest ones. The final order is applied in list_images().
SORT_ORDERS = {
    "name": "name_natural",
    "newest": "modifiedTime desc,name_natural desc",
    "oldest": "modifiedTime desc,name_natural desc",
}
DEFAULT_SORT = "oldest"   # chronological: today's images, then tomorrow's

_STRIPE_LOCKS = [threading.Lock() for _ in range(64)]
_REFRESH_LOCK = threading.Lock()
_INDEX_LOCK = threading.RLock()

# Persistent worker threads for concurrent Drive calls.
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ib")
_TLS = threading.local()
_ROOT_IDS = {}
SERVICE_TTL = 15 * 60

INDEX_MAX_AGE = 24 * 60 * 60     # a saved listing may be shown instantly for a day
LIST_PAGE_SIZE = 1000            # Drive's maximum: one round trip for MAX_IMAGES


class NotConnected(Exception):
    pass


class NotFound(Exception):
    pass


class TooLarge(Exception):
    pass


def valid_id(value) -> bool:
    return isinstance(value, str) and bool(ID_RE.match(value))


def _stripe_lock(key: str) -> threading.Lock:
    return _STRIPE_LOCKS[hash(key) % len(_STRIPE_LOCKS)]


def _base_id(p: Path) -> str:
    """'abc.jpg' -> 'abc'; 'abc~p.webp' -> 'abc'."""
    return p.stem.split(PREVIEW_TAG)[0]


class ChartBrowserEngine:
    SCOPES = BROWSER_SCOPES

    # --- Auth ---
    @staticmethod
    def has_scope(creds) -> bool:
        if not creds:
            return False
        scopes = set(getattr(creds, 'scopes', None) or [])
        return bool(scopes & {BROWSER_SCOPES[0], FULL_DRIVE_SCOPE})

    @staticmethod
    def get_creds(uid, user_data=None):
        if user_data is None:
            user_data = get_user_keys(uid)
        token_json = (user_data or {}).get(TOKEN_FIELD)
        if not token_json:
            return None
        try:
            creds = Credentials.from_authorized_user_info(json.loads(token_json))
        except Exception as e:
            print(f"Chart Browser token load error: {e}")
            return None

        if creds.expired and creds.refresh_token:
            # Prefetching fires several requests at once; without this each one
            # refreshed the token and wrote to Firestore on its own.
            with _REFRESH_LOCK:
                latest = (get_user_keys(uid) or {}).get(TOKEN_FIELD)
                reused = False
                if latest and latest != token_json:
                    try:
                        newer = Credentials.from_authorized_user_info(json.loads(latest))
                        if not newer.expired:
                            creds, reused = newer, True
                    except Exception:
                        pass
                if not reused:
                    try:
                        creds.refresh(GoogleAuthRequest())
                        update_user_keys(uid, {TOKEN_FIELD: creds.to_json()})
                    except Exception as e:
                        print(f"Chart Browser token refresh error: {e}")
                        return None

        if not ChartBrowserEngine.has_scope(creds):
            return None
        return creds

    @staticmethod
    def get_service(creds, uid=None):
        """Build the Drive client. With uid, reuse one per thread: constructing
        it parses the whole Drive API description and costs real time on every
        request."""
        if uid is None:
            return build('drive', 'v3', credentials=creds, cache_discovery=False)
        cache = getattr(_TLS, "services", None)
        if cache is None:
            cache = _TLS.services = {}
        sig = (creds.token, str(creds.expiry))
        entry = cache.get(uid)
        if entry and entry[0] == sig and time.time() - entry[1] < SERVICE_TTL:
            return entry[2]
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        if len(cache) > 32:
            cache.clear()
        cache[uid] = (sig, time.time(), service)
        return service

    @staticmethod
    def run_parallel(*fns):
        """Run zero-arg callables on the worker pool; re-raises the first error."""
        futures = [_POOL.submit(fn) for fn in fns]
        return [f.result() for f in futures]

    @staticmethod
    def disconnect(uid):
        _ROOT_IDS.pop(uid, None)
        update_user_keys(uid, {
            TOKEN_FIELD: firestore.DELETE_FIELD,
            LAST_FOLDER_FIELD: firestore.DELETE_FIELD,
        })
        ChartBrowserEngine.purge_cache(uid)

    # --- Drive listing ---
    @staticmethod
    def _list_all(service, q, fields, order_by, page_size, limit):
        items = []
        token = None
        truncated = False
        while True:
            resp = service.files().list(
                q=q,
                fields=f"nextPageToken, files({fields})",
                orderBy=order_by,
                pageSize=page_size,
                pageToken=token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            items.extend(resp.get('files', []))
            token = resp.get('nextPageToken')
            if len(items) >= limit:
                truncated = bool(token) or len(items) > limit
                items = items[:limit]
                break
            if not token:
                break
        return items, truncated

    @staticmethod
    def folder_info(service, folder_id, uid=None):
        if folder_id == 'root':
            return {"id": "root", "name": "My Drive", "parent": None}
        if folder_id == 'shared':
            return {"id": "shared", "name": "Shared with me", "parent": None}

        meta = service.files().get(
            fileId=folder_id,
            fields='id,name,mimeType,parents',
            supportsAllDrives=True,
        ).execute()
        if meta.get('mimeType') != FOLDER_MIME:
            raise NotFound("Not a folder")

        parents = meta.get('parents') or []
        parent = None
        if parents:
            root_id = _ROOT_IDS.get(uid) if uid else None
            if not root_id:
                root_id = service.files().get(fileId='root', fields='id').execute().get('id')
                if uid:
                    _ROOT_IDS[uid] = root_id
            parent = 'root' if parents[0] == root_id else parents[0]
        else:
            parent = 'shared'
        return {"id": meta['id'], "name": meta.get('name', 'Folder'), "parent": parent}

    @staticmethod
    def list_folders(service, parent_id):
        if parent_id == 'shared':
            q = f"sharedWithMe=true and mimeType='{FOLDER_MIME}' and trashed=false"
        else:
            q = f"'{parent_id}' in parents and mimeType='{FOLDER_MIME}' and trashed=false"
        folders, _ = ChartBrowserEngine._list_all(
            service, q, 'id,name', 'name_natural', LIST_PAGE_SIZE, 600
        )
        return folders

    @staticmethod
    def list_images(service, folder_id, sort=DEFAULT_SORT):
        mime_clause = " or ".join(f"mimeType='{m}'" for m in IMAGE_MIME_EXT)
        q = f"'{folder_id}' in parents and trashed=false and ({mime_clause})"
        fields = ("id,name,mimeType,size,modifiedTime,webViewLink,"
                  "imageMediaMetadata(width,height)")
        raw, truncated = ChartBrowserEngine._list_all(
            service, q, fields, SORT_ORDERS.get(sort, SORT_ORDERS[DEFAULT_SORT]), LIST_PAGE_SIZE, MAX_IMAGES
        )
        images = []
        for f in raw:
            meta = f.get('imageMediaMetadata') or {}
            try:
                size = int(f.get('size') or 0)
            except (TypeError, ValueError):
                size = 0
            images.append({
                "id": f['id'],
                "name": f.get('name', f['id']),
                "mime": f.get('mimeType'),
                "size": size,
                "modified": f.get('modifiedTime'),
                "width": meta.get('width'),
                "height": meta.get('height'),
                "link": f.get('webViewLink'),
            })
        if sort in ("oldest", "newest"):
            # Sort here as well so the order never depends on Drive's tie
            # handling: by modified time, then name for identical timestamps.
            images.sort(key=lambda i: (i.get("modified") or "", i["name"].lower()),
                        reverse=(sort == "newest"))
        return images, truncated

    # --- Temp cache ---
    @staticmethod
    def cache_dir(uid) -> Path:
        d = get_user_temp_dir(uid) / CACHE_SUBDIR
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def load_index(uid) -> dict:
        path = ChartBrowserEngine.cache_dir(uid) / INDEX_NAME
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def save_index(uid, folder, images, sort=None, parent=None, truncated=False):
        with _INDEX_LOCK:
            d = ChartBrowserEngine.cache_dir(uid)
            old = ChartBrowserEngine.load_index(uid)
            old_mod = {}
            if (old.get("folder") or {}).get("id") == folder["id"]:
                old_mod = {i["id"]: i.get("modified") for i in old.get("images", [])}

            keep = {i["id"] for i in images if i["id"] in old_mod and old_mod[i["id"]] == i.get("modified")}
            for p in d.iterdir():
                # never delete the index itself or a download/preview that is
                # being written right now (a background re-list can overlap)
                if p.name == INDEX_NAME or p.suffix in (".part", ".tmp"):
                    continue
                if _base_id(p) not in keep:
                    try:
                        p.unlink()
                    except OSError:
                        pass

            tmp = d / (INDEX_NAME + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "folder": folder, "images": images, "saved": time.time(),
                    "sort": sort, "parent": parent, "truncated": bool(truncated),
                }, f)
            os.replace(tmp, d / INDEX_NAME)

    @staticmethod
    def load_cached_listing(uid, folder_id, sort):
        """The last saved listing for this folder+sort, if recent enough to
        show immediately while the client re-checks Drive in the background."""
        idx = ChartBrowserEngine.load_index(uid)
        if (idx.get("folder") or {}).get("id") != folder_id:
            return None
        if idx.get("sort") != sort or "parent" not in idx:
            return None
        if time.time() - idx.get("saved", 0) > INDEX_MAX_AGE:
            return None
        return idx

    @staticmethod
    def warm(uid, folder_id, sort=DEFAULT_SORT):
        """Fire-and-forget: refresh the token, build the Drive client, make sure
        a recent listing is on disk and pull the first images, so the Image
        Browser opens onto warm data."""
        def job():
            try:
                creds = ChartBrowserEngine.get_creds(uid)
                if not creds:
                    return
                service = ChartBrowserEngine.get_service(creds, uid)
                idx = ChartBrowserEngine.load_cached_listing(uid, folder_id, sort)
                if idx and time.time() - idx.get("saved", 0) < 300:
                    images = idx["images"]
                else:
                    folder = ChartBrowserEngine.folder_info(service, folder_id, uid)
                    images, truncated = ChartBrowserEngine.list_images(service, folder_id, sort)
                    ChartBrowserEngine.purge_stale(uid)
                    ChartBrowserEngine.save_index(
                        uid, {"id": folder["id"], "name": folder["name"]}, images,
                        sort=sort, parent=folder["parent"], truncated=truncated)
                for item in images[:2]:
                    try:
                        ChartBrowserEngine.get_preview_file(uid, item["id"])
                    except Exception:
                        pass
            except Exception as e:
                print(f"Chart Browser warm-up skipped: {e}")
        threading.Thread(target=job, daemon=True, name="cb-warm").start()

    @staticmethod
    def cached_ids(uid, images):
        d = ChartBrowserEngine.cache_dir(uid)
        present = {_base_id(p) for p in d.iterdir() if p.suffix in IMAGE_MIME_EXT.values() and p.stat().st_size > 0}
        return [i["id"] for i in images if i["id"] in present]

    @staticmethod
    def purge_cache(uid, keep_index=False) -> int:
        d = get_user_temp_dir(uid) / CACHE_SUBDIR
        if not d.exists():
            return 0
        freed = 0
        for p in d.iterdir():
            if keep_index and p.name == INDEX_NAME:
                continue
            try:
                freed += p.stat().st_size
                p.unlink()
            except OSError:
                pass
        if not keep_index:
            shutil.rmtree(d, ignore_errors=True)
        return freed

    @staticmethod
    def purge_stale(uid):
        d = ChartBrowserEngine.cache_dir(uid)
        cutoff = time.time() - CACHE_TTL_SECONDS
        for p in d.iterdir():
            if p.name == INDEX_NAME:
                continue
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass

    @staticmethod
    def _enforce_cap(d: Path, protect: str):
        files = []
        total = 0
        for p in d.iterdir():
            if p.name == INDEX_NAME or p.suffix == ".part":
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        if total <= MAX_CACHE_BYTES:
            return
        files.sort(key=lambda t: t[0])
        for _, size, p in files:
            if total <= MAX_CACHE_BYTES:
                break
            if p.name == protect:
                continue
            try:
                p.unlink()
                total -= size
            except OSError:
                pass

    @staticmethod
    def get_image_file(uid, file_id):
        if not valid_id(file_id):
            raise NotFound("Bad id")

        index = ChartBrowserEngine.load_index(uid)
        entry = next((i for i in index.get("images", []) if i["id"] == file_id), None)
        if entry is None:
            raise NotFound("Image is not in the open folder")

        mime = entry.get("mime")
        ext = IMAGE_MIME_EXT.get(mime)
        if ext is None:
            raise NotFound("Unsupported type")
        if entry.get("size", 0) > MAX_FILE_BYTES:
            raise TooLarge("Image is too large to preview")

        d = ChartBrowserEngine.cache_dir(uid)
        final = d / f"{file_id}{ext}"
        if final.exists() and final.stat().st_size > 0:
            os.utime(final, None)
            return final, mime, entry

        with _stripe_lock(f"{uid}:{file_id}"):
            if final.exists() and final.stat().st_size > 0:
                return final, mime, entry

            creds = ChartBrowserEngine.get_creds(uid)
            if not creds:
                raise NotConnected()
            service = ChartBrowserEngine.get_service(creds, uid)

            part = d / f"{file_id}.part"
            try:
                request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
                with open(part, "wb") as fh:
                    downloader = MediaIoBaseDownload(fh, request, chunksize=DOWNLOAD_CHUNK)
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()
                        if fh.tell() > MAX_FILE_BYTES:
                            raise TooLarge("Image is too large to preview")
                os.replace(part, final)
            except HttpError as e:
                if e.resp.status == 404:
                    raise NotFound("Image no longer exists")
                raise
            finally:
                if part.exists():
                    try:
                        part.unlink()
                    except OSError:
                        pass

        ChartBrowserEngine._enforce_cap(d, final.name)
        return final, mime, entry

    @staticmethod
    def get_preview_file(uid, file_id):
        """Return (path, mime, entry) for a screen-sized WebP of the image.
        """
        path, mime, entry = ChartBrowserEngine.get_image_file(uid, file_id)
        if mime == "image/gif" or entry.get("size", 0) < PREVIEW_MIN_BYTES:
            return path, mime, entry

        d = path.parent
        prev = d / f"{file_id}{PREVIEW_TAG}.webp"
        if prev.exists() and prev.stat().st_size > 0:
            os.utime(prev, None)
            return prev, "image/webp", entry

        with _stripe_lock(f"{uid}:{file_id}:p"):
            if prev.exists() and prev.stat().st_size > 0:
                return prev, "image/webp", entry
            tmp = d / f"{file_id}{PREVIEW_TAG}.part"
            try:
                with Image.open(path) as im:
                    im = ImageOps.exif_transpose(im)
                    if max(im.size) <= PREVIEW_MAX_PX:
                        return path, mime, entry
                    im.thumbnail((PREVIEW_MAX_PX, PREVIEW_MAX_PX), Image.LANCZOS)
                    if im.mode not in ("RGB", "RGBA"):
                        im = im.convert("RGBA" if "transparency" in im.info else "RGB")
                    im.save(tmp, "WEBP", quality=PREVIEW_QUALITY, method=4)
                os.replace(tmp, prev)
            except Exception as e:
                print(f"Chart Browser preview fallback for {file_id}: {e}")
                return path, mime, entry
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
        return prev, "image/webp", entry