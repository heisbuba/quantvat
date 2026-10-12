import threading
import sys
import os
import json
import uuid
import time
import contextvars
import traceback
import atexit
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
import datetime

# --- Global State ---
USER_LOGS = {} 
USER_PROGRESS = {}
USER_LOG_SEQ = {}      # per-run log sequence; the client cursor is a seq id, not a list index
_USER_ACTIVITY = {}
_RUN_STATE_TTL = 3600  # seconds before an idle run's logs/progress are evicted
LOCK = threading.RLock()

# --- Configuration Constants ---
TEMP_DIR = Path("/tmp")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# --- Helper Functions for State ---
def get_progress(uid):
    with LOCK:
        # Copy: callers add keys to the result
        return dict(USER_PROGRESS.get(uid, {"percent": 0, "text": "System Idle", "status": "idle", "run_id": None}))

def update_progress(uid, percent, text, status):
    with LOCK:
        # Preserve whatever run_id is already associated with this uid 
        existing_run_id = USER_PROGRESS.get(uid, {}).get("run_id")
        USER_PROGRESS[uid] = {"percent": percent, "text": text, "status": status, "run_id": existing_run_id}
        _USER_ACTIVITY[uid] = time.time()

def _evict_stale_runs(now):
    """Drop state of idle users. Caller must hold LOCK."""
    for old_uid, ts in list(_USER_ACTIVITY.items()):
        if now - ts > _RUN_STATE_TTL and old_uid not in ACTIVE_TASKS:
            USER_LOGS.pop(old_uid, None)
            USER_PROGRESS.pop(old_uid, None)
            USER_LOG_SEQ.pop(old_uid, None)
            _USER_ACTIVITY.pop(old_uid, None)

def start_new_run(uid) -> str:
    """Call this exactly once at the start of a new background run"""
    run_id = uuid.uuid4().hex
    with LOCK:
        now = time.time()
        _evict_stale_runs(now)
        USER_LOGS[uid] = []
        USER_LOG_SEQ[uid] = 0
        USER_PROGRESS[uid] = {"percent": 5, "text": "Initializing Engine...", "status": "active", "run_id": run_id}
        _USER_ACTIVITY[uid] = now
    return run_id

def get_user_temp_dir(uid) -> Path:
    """Creates and returns a specific directory for the logged-in user."""
    user_dir = TEMP_DIR / uid
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir

# --- Pending File Manifest ---
MANIFEST_FILENAME = ".manifest.json"

def _manifest_path(uid) -> Path:
    return get_user_temp_dir(uid) / MANIFEST_FILENAME

def _read_manifest(uid) -> dict:
    path = _manifest_path(uid)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _write_manifest(uid, data: dict):
    path = _manifest_path(uid)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, path) 
    except Exception as e:
        print(f"   Could not write manifest for {uid}: {e}")

def set_pending_file(uid, role: str, path: Path):
    with LOCK:
        data = _read_manifest(uid)
        old_path_str = data.get(role)
        if old_path_str and old_path_str != str(path):
            old_path = Path(old_path_str)
            if old_path.exists():
                try:
                    old_path.unlink()
                    print(f"   Removed superseded {role} file: {old_path.name}")
                except Exception as e:
                    print(f"   Could not remove superseded {role} file: {e}")
        data[role] = str(path)
        _write_manifest(uid, data)

def get_pending_files(uid) -> dict:
    with LOCK:
        data = _read_manifest(uid)
    result = {}
    for role, p in data.items():
        path = Path(p)
        if path.exists():
            result[role] = path
    return result

def clear_pending_file(uid, role: str):
    with LOCK:
        data = _read_manifest(uid)
        if role in data:
            data.pop(role, None)
            _write_manifest(uid, data)

# --- Per-user Task Lock ---
ACTIVE_TASKS = {} 

def try_start_task(uid, task_name: str) -> bool:
    with LOCK:
        if uid in ACTIVE_TASKS:
            return False
        ACTIVE_TASKS[uid] = task_name
        return True

def end_task(uid):
    with LOCK:
        ACTIVE_TASKS.pop(uid, None)

def get_active_task(uid) -> Optional[str]:
    with LOCK:
        return ACTIVE_TASKS.get(uid)

# --- Log Capture System ---
CURRENT_LOG_USER: contextvars.ContextVar = contextvars.ContextVar("current_log_user", default=None)
_ERROR_BATCH_WINDOW = 2.0
_ERROR_BATCH_CAP = 50
_error_batches = {}
_error_batches_lock = threading.Lock()

# Output produced while persisting an error must not be re-captured,
# or a failing sink feeds itself.
_SUPPRESS = threading.local()

@contextmanager
def _suppressed():
    prev = getattr(_SUPPRESS, "on", False)
    _SUPPRESS.on = True
    try:
        yield
    finally:
        _SUPPRESS.on = prev


def submit_with_context(executor, fn, *args, **kwargs):
    """executor.submit() that carries contextvars into the pool thread.
    One context copy per call: a Context can't be entered by two threads at once."""
    ctx = contextvars.copy_context()
    return executor.submit(ctx.run, fn, *args, **kwargs)


def _flush_error_batch(uid):
    with _error_batches_lock:
        batch = _error_batches.pop(uid, None)
    if not batch:
        return
    collapsed = []
    for line in batch["msgs"]:
        if not collapsed or collapsed[-1] != line:
            collapsed.append(line)
    try:
        from .config import log_error
        with _suppressed():
            log_error(
                source="logcatcher",
                message="\n".join(collapsed)[:1900],
                uid=uid,
            )
    except Exception:
        pass  


def flush_pending_errors():
    """Persist batched errors now (registered with atexit)."""
    with _error_batches_lock:
        uids = list(_error_batches.keys())
        for u in uids:
            timer = _error_batches[u].get("timer")
            if timer is not None:
                timer.cancel()
    for u in uids:
        try:
            _flush_error_batch(u)
        except Exception:
            pass

atexit.register(flush_pending_errors)


def _queue_error_persist(uid, msg):
    with _error_batches_lock:
        batch = _error_batches.get(uid)
        if batch is None:
            batch = _error_batches[uid] = {"msgs": [], "timer": None}
        batch["msgs"].append(msg)
        if len(batch["msgs"]) >= _ERROR_BATCH_CAP:
            timer = batch["timer"]
            batch["timer"] = None
            if timer is not None:
                timer.cancel()
            flush_now = True
        else:
            flush_now = False
            if batch["timer"] is None:
                timer = threading.Timer(_ERROR_BATCH_WINDOW, _flush_error_batch, args=(uid,))
                timer.daemon = True
                batch["timer"] = timer
                timer.start()
    if flush_now:
        _flush_error_batch(uid)


class LogCatcher:

    def __init__(self, original_stream, is_stderr=False):
        self.terminal = original_stream
        self.is_stderr = is_stderr

    @staticmethod
    def _uid_from_thread_name():
        name = threading.current_thread().name
        return name.replace("user_", "") if name.startswith("user_") else None

    _ERROR_KEYS = ("error", "critical", "exception", "traceback", "failed",
                   "fatal", "aborting", "\u274c")
    _WARN_KEYS = ("warn", "\u26a0", "retry", "back off", "could not",
                  "unable to", "skipped")

    def _infer_level(self, msg):
        m = msg.lower()
        if any(k in m for k in self._ERROR_KEYS):
            return "error"
        if any(k in m for k in self._WARN_KEYS):
            return "warning"
        return "info"

    def write(self, msg):
        self.terminal.write(msg)
        if not msg or not msg.strip():
            return
        if getattr(_SUPPRESS, "on", False):
            return
        uid = CURRENT_LOG_USER.get() or self._uid_from_thread_name()
        if not uid:
            return
        level = self._infer_level(msg)
        # stderr isn't automatically an error (library warnings land there);
        # uncaught exceptions are recorded by the excepthooks below.
        if self.is_stderr and level == "info":
            level = "warning"
        with LOCK:
            seq = USER_LOG_SEQ.get(uid, 0) + 1
            USER_LOG_SEQ[uid] = seq
            entry = {
                "seq": seq,
                "ts": time.time(),
                "level": level,
                "msg": msg.rstrip("\n"),
            }
            _USER_ACTIVITY[uid] = entry["ts"]
            log = USER_LOGS.setdefault(uid, [])
            log.append(entry)
            if len(log) > 500:
                del log[:len(log) - 500]
        # Firestore I/O deliberately happens OUTSIDE the global state LOCK.
        if entry["level"] == "error":
            _queue_error_persist(uid, entry["msg"])

    def flush(self):
        self.terminal.flush()

    def __getattr__(self, name):
        terminal = self.__dict__.get("terminal")
        if terminal is None:
            raise AttributeError(name)
        return getattr(terminal, name)


def _install_excepthooks():
    original_sys_hook = sys.excepthook
    original_thread_hook = threading.excepthook

    def _record(exc_type, exc_value, exc_traceback):
        try:
            text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))[:1900]
            uid = CURRENT_LOG_USER.get() or LogCatcher._uid_from_thread_name()
            from .config import log_error
            with _suppressed():
                log_error(source=f"excepthook:{threading.current_thread().name}", message=text, uid=uid)
        except Exception:
            pass

    def sys_hook(exc_type, exc_value, exc_traceback):
        _record(exc_type, exc_value, exc_traceback)
        original_sys_hook(exc_type, exc_value, exc_traceback)

    def thread_hook(args):
        if args.exc_type is not None:
            _record(args.exc_type, args.exc_value, args.exc_traceback)
        original_thread_hook(args)

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook


# Tee stdout AND stderr
sys.stdout = LogCatcher(sys.stdout)
sys.stderr = LogCatcher(sys.stderr, is_stderr=True)
_install_excepthooks()