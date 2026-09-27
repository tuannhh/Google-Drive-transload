import base64
import json
import logging
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from . import config, db, drive, links
from .worker import Worker, human_bytes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

STATIC = Path(__file__).parent / "static"
worker = Worker()
_oauth_states: dict[str, float] = {}
_quota_cache: dict[int, tuple[float, dict]] = {}


@asynccontextmanager
async def lifespan(_app):
    db.init()
    worker.start()
    if not config.GOOGLE_CLIENT_ID:
        log.warning("Chưa cấu hình GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET trong file .env")
    yield


app = FastAPI(title="Google Drive Transload", lifespan=lifespan)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if config.APP_PASSWORD and request.url.path != "/healthz":
        ok = False
        h = request.headers.get("authorization", "")
        if h.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(h[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, config.APP_USERNAME) and secrets.compare_digest(pw, config.APP_PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return PlainTextResponse("Cần đăng nhập", status_code=401,
                                     headers={"WWW-Authenticate": 'Basic realm="Drive Transload"'})
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def get_config():
    return {"configured": bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET),
            "redirect_uri": f"{config.BASE_URL}/oauth/callback", "base_url": config.BASE_URL}


# ---------------- OAuth ----------------

@app.get("/oauth/start")
def oauth_start():
    if not config.GOOGLE_CLIENT_ID:
        raise HTTPException(400, "Chưa cấu hình GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET")
    now = time.time()
    for k, t in list(_oauth_states.items()):
        if now - t > 900:
            _oauth_states.pop(k, None)
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = now
    return RedirectResponse(drive.auth_url(state))


@app.get("/oauth/callback")
def oauth_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse(f"/?login_error={error}")
    if state not in _oauth_states:
        return RedirectResponse("/?login_error=state_invalid")
    _oauth_states.pop(state, None)
    tok = drive.exchange_code(code)
    rt = tok.get("refresh_token")
    if not rt:
        return RedirectResponse("/?login_error=no_refresh_token")
    about = drive.Drive(rt).about()
    user = about.get("user", {})
    acc_id = db.upsert_account(user.get("emailAddress", "unknown"), user.get("displayName", ""), rt)
    _quota_cache.pop(acc_id, None)
    return RedirectResponse(f"/?login_ok={user.get('emailAddress', '')}")


# ---------------- Accounts ----------------

def _account_info(row) -> dict:
    info = {"id": row["id"], "email": row["email"], "display_name": row["display_name"]}
    cached = _quota_cache.get(row["id"])
    if cached and time.time() - cached[0] < 60:
        return {**info, **cached[1]}
    try:
        limit, usage = drive.quota_of(drive.Drive.for_account(row["id"]).about())
        q = {"limit": limit, "usage": usage, "free": None if limit is None else max(0, limit - usage),
             "limit_h": human_bytes(limit), "usage_h": human_bytes(usage),
             "free_h": human_bytes(None if limit is None else max(0, limit - usage)), "ok": True}
    except Exception as e:
        q = {"ok": False, "error": f"Không lấy được thông tin (cần đăng nhập lại?): {e}"}
    _quota_cache[row["id"]] = (time.time(), q)
    return {**info, **q}


@app.get("/api/accounts")
def api_accounts():
    return [_account_info(r) for r in db.list_accounts()]


@app.delete("/api/accounts/{account_id}")
def api_delete_account(account_id: int):
    used = db.q1("SELECT COUNT(*) AS n FROM jobs WHERE (source_account_id=? OR dest_account_id=?) "
                 "AND status IN ('scanning','running','throttled','awaiting_confirm')", (account_id, account_id))["n"]
    if used:
        raise HTTPException(400, "Tài khoản đang được dùng bởi job chưa xong")
    db.delete_account(account_id)
    _quota_cache.pop(account_id, None)
    return {"ok": True}


# ---------------- Links ----------------

@app.post("/api/parse-links")
async def api_parse_links(text: str = Form(""), file: UploadFile | None = File(None)):
    lines = [text]
    if file is not None and file.filename:
        data = await file.read()
        try:
            lines.extend(links.extract_from_file(file.filename, data))
        except Exception as e:
            raise HTTPException(400, f"Không đọc được file {file.filename}: {e}")
    refs, bad = links.parse_many("\n".join(lines))
    return {"refs": [{"id": r.id, "resource_key": r.resource_key, "raw": r.raw} for r in refs], "bad": bad}


# ---------------- Jobs ----------------

class JobIn(BaseModel):
    mode: str
    dest_account_id: int
    source_account_id: int | None = None
    refs: list[dict] = []
    whole_drive: bool = False
    dest_folder: str = ""
    dest_root_name: str = ""
    auto_start: bool = True
    name: str = ""


@app.post("/api/jobs")
def api_create_job(j: JobIn):
    if j.mode not in ("public", "account"):
        raise HTTPException(400, "mode không hợp lệ")
    if not db.get_account(j.dest_account_id):
        raise HTTPException(400, "Chưa chọn tài khoản đích")
    if j.mode == "account":
        if not j.source_account_id or not db.get_account(j.source_account_id):
            raise HTTPException(400, "Chưa chọn tài khoản nguồn")
        if j.source_account_id == j.dest_account_id:
            raise HTTPException(400, "Tài khoản nguồn và đích phải khác nhau")
    else:
        j.whole_drive = False
        j.source_account_id = None
    if not j.refs and not j.whole_drive:
        raise HTTPException(400, "Chưa có link nào")
    dest_parent = None
    if j.dest_folder.strip():
        ref = links.parse_link(j.dest_folder)
        if not ref:
            raise HTTPException(400, "Link thư mục đích không hợp lệ")
        dest_parent = ref.id
    stamp = time.strftime("%Y-%m-%d %H%M")
    root_name = j.dest_root_name.strip() or f"Transload {stamp}"
    name = j.name.strip() or (("Toàn bộ My Drive + " if j.whole_drive else "") + f"{len(j.refs)} link – {stamp}")
    refs = [{"id": r["id"], "resource_key": r.get("resource_key"), "raw": r.get("raw", r["id"])} for r in j.refs]
    with db.tx() as c:
        cur = c.execute(
            """INSERT INTO jobs(name, mode, source_account_id, dest_account_id, inputs, whole_drive, dest_parent_id,
                   dest_root_name, auto_start, status, status_message, created_at)
               VALUES (?,?,?,?,?,?,?,?,?, 'scanning', 'Đang chờ quét...', ?)""",
            (name, j.mode, j.source_account_id, j.dest_account_id, json.dumps(refs, ensure_ascii=False),
             int(j.whole_drive), dest_parent, root_name, int(j.auto_start), db.now()))
        job_id = cur.lastrowid
    worker.wake()
    return {"id": job_id}


def _job_out(row) -> dict:
    d = dict(row)
    d.pop("inputs", None)
    d["stats"] = s = db.job_stats(row["id"])
    s["bytes_total_h"] = human_bytes(s["bytes_total"])
    s["bytes_done_h"] = human_bytes(s["bytes_done"])
    free = row["dest_free_bytes"]
    d["dest_free_h"] = "chưa kiểm tra" if free is None else "không giới hạn" if free < 0 else human_bytes(free)
    pending_bytes = s["bytes_total"] - s["bytes_done"]
    d["insufficient"] = free is not None and free >= 0 and row["status"] in ("awaiting_confirm", "paused_quota") \
        and pending_bytes > free
    for k in ("source_account_id", "dest_account_id"):
        acc = db.get_account(row[k]) if row[k] else None
        d[k.replace("_id", "_email")] = acc["email"] if acc else None
    return d


@app.get("/api/jobs")
def api_jobs():
    return [_job_out(r) for r in db.q("SELECT * FROM jobs ORDER BY id DESC")]


@app.get("/api/jobs/{job_id}")
def api_job(job_id: int):
    row = db.get_job(job_id)
    if not row:
        raise HTTPException(404)
    return _job_out(row)


class ActionIn(BaseModel):
    action: str


def _resume_status(job_id: int) -> str:
    job = db.get_job(job_id)
    unlisted = db.q1("SELECT COUNT(*) AS n FROM items WHERE job_id=? AND is_folder=1 AND listed=0 AND status!='failed'",
                     (job_id,))["n"]
    return "scanning" if (not job["roots_ready"] or unlisted) else "running"


@app.post("/api/jobs/{job_id}/action")
def api_job_action(job_id: int, a: ActionIn):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    st = job["status"]
    if a.action == "pause":
        if st in ("scanning", "running", "throttled", "awaiting_confirm"):
            db.set_job(job_id, status="paused", status_message="Đã tạm dừng")
    elif a.action in ("start", "resume"):
        if st in ("paused", "awaiting_confirm", "paused_quota", "error", "throttled"):
            ns = _resume_status(job_id)
            db.set_job(job_id, status=ns, wait_until=None, status_message="Đang tiếp tục...")
    elif a.action == "recheck":  # quét lại dung lượng đích
        if st in ("awaiting_confirm", "paused_quota", "paused", "error"):
            db.set_job(job_id, status="scanning", auto_start=1, status_message="Kiểm tra lại dung lượng...")
    elif a.action == "cancel":
        if st not in ("completed", "completed_with_errors"):
            db.set_job(job_id, status="cancelled", status_message="Đã huỷ")
    elif a.action == "retry_failed":
        db.execute("UPDATE items SET status='pending', attempts=0, error=NULL, maybe_done=1, "
                   "listed=CASE WHEN is_folder=1 AND dest_id IS NULL THEN 0 ELSE listed END "
                   "WHERE job_id=? AND status='failed'", (job_id,))
        db.set_job(job_id, status=_resume_status(job_id), finished_at=None, status_message="Thử lại các mục lỗi...")
    else:
        raise HTTPException(400, "action không hợp lệ")
    worker.wake()
    return _job_out(db.get_job(job_id))


@app.delete("/api/jobs/{job_id}")
def api_delete_job(job_id: int):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    if job["status"] in ("scanning", "running"):
        raise HTTPException(400, "Hãy tạm dừng hoặc huỷ job trước khi xoá")
    with db.tx() as c:
        c.execute("DELETE FROM items WHERE job_id=?", (job_id,))
        c.execute("DELETE FROM shared_perms WHERE job_id=?", (job_id,))
        c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    return {"ok": True}


def _log_path(job_id: int) -> Path:
    return config.LOG_DIR / f"job_{job_id}.txt"


@app.get("/api/jobs/{job_id}/log")
def api_job_log(job_id: int):
    p = _log_path(job_id)
    if not p.exists():
        return PlainTextResponse("(chưa có log)")
    return FileResponse(p, media_type="text/plain; charset=utf-8", filename=f"transload_job_{job_id}.txt")


@app.get("/api/jobs/{job_id}/log/tail")
def api_job_log_tail(job_id: int, lines: int = 80):
    p = _log_path(job_id)
    if not p.exists():
        return JSONResponse({"lines": []})
    with open(p, encoding="utf-8", errors="replace") as f:
        tail = deque(f, maxlen=min(max(lines, 1), 500))
    return {"lines": [l.rstrip("\n") for l in tail]}
