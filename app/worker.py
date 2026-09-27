"""Worker chạy nền: quét nguồn -> kiểm tra dung lượng -> tạo thư mục -> copy file phía server Google.

Mọi trạng thái nằm trong SQLite nên container khởi động lại sẽ tự chạy tiếp.

Trạng thái job:
  scanning -> awaiting_confirm | running -> completed | completed_with_errors
  running -> throttled (tự chạy lại sau wait_until) | paused_quota | paused | cancelled | error
"""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from google.auth.exceptions import RefreshError

from . import config, db
from .drive import FOLDER_MIME, SHORTCUT_MIME, UNCOPYABLE_MIMES, Drive, DriveError, quota_of
from .links import DriveRef

log = logging.getLogger("worker")

RUNNABLE = ("scanning", "running")
PERMANENT_REASONS = {"cannotCopyFile", "notFound", "fileNotDownloadable", "insufficientFilePermissions",
                     "cannotDownloadAbusiveFile", "forbidden", "appNotAuthorizedToFile"}


def human_bytes(n: int | None) -> str:
    if n is None:
        return "không giới hạn"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.2f} TB"


class JobLog:
    """Ghi log .txt cho từng job: data/logs/job_<id>.txt"""
    _locks: dict[int, threading.Lock] = {}

    def __init__(self, job_id: int):
        self.path = config.LOG_DIR / f"job_{job_id}.txt"
        self.lock = JobLog._locks.setdefault(job_id, threading.Lock())

    def write(self, level: str, msg: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {msg}\n"
        with self.lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(line)
        log.info("job log %s: %s", self.path.name, line.strip())


class StopJob(Exception):
    def __init__(self, status: str, message: str, wait_until: float | None = None):
        self.status, self.message, self.wait_until = status, message, wait_until


class Worker:
    def __init__(self):
        self._thread = threading.Thread(target=self.run_forever, name="transload-worker", daemon=True)
        self._wake = threading.Event()

    def start(self):
        # Item đang copy dở khi container tắt -> đánh dấu cần kiểm tra trùng khi chạy lại
        db.execute("UPDATE items SET status='pending', maybe_done=1 WHERE status='in_progress'")
        self._thread.start()

    def wake(self):
        self._wake.set()

    def run_forever(self):
        while True:
            try:
                job = self._next_job()
                if job is None:
                    self._wake.wait(5)
                    self._wake.clear()
                    continue
                self.process(job["id"])
            except Exception:  # không để worker chết
                log.exception("worker loop error")
                time.sleep(10)

    def _next_job(self):
        now = db.now()
        db.execute("UPDATE jobs SET status='running', wait_until=NULL, status_message='Tiếp tục sau khi tạm nghỉ' "
                   "WHERE status='throttled' AND wait_until <= ?", (now,))
        return db.q1(f"SELECT * FROM jobs WHERE status IN ({','.join('?' * len(RUNNABLE))}) ORDER BY id LIMIT 1",
                     RUNNABLE)

    # ------------------------------------------------------------------
    def process(self, job_id: int):
        job = db.get_job(job_id)
        jl = JobLog(job_id)
        try:
            if job["status"] == "scanning":
                self.scan(job, jl)
            job = db.get_job(job_id)
            if job["status"] == "running":
                self.transfer(job, jl)
        except StopJob as s:
            db.set_job(job_id, status=s.status, status_message=s.message, wait_until=s.wait_until)
            jl.write("DỪNG" if s.status != "throttled" else "TẠM NGHỈ", s.message)
        except RefreshError as e:
            msg = f"Phiên đăng nhập Google đã hết hạn/bị thu hồi, hãy đăng nhập lại tài khoản rồi bấm Tiếp tục. ({e})"
            db.set_job(job_id, status="error", status_message=msg)
            jl.write("LỖI", msg)
        except Exception as e:
            log.exception("job %s failed", job_id)
            db.set_job(job_id, status="error", status_message=f"Lỗi không mong muốn: {e}")
            jl.write("LỖI", f"Lỗi không mong muốn: {e!r}")

    def _check_control(self, job_id: int):
        st = db.q1("SELECT status FROM jobs WHERE id=?", (job_id,))["status"]
        if st in ("paused", "cancelled"):
            raise StopJob(st, "Người dùng đã tạm dừng" if st == "paused" else "Người dùng đã huỷ")

    def _clients(self, job):
        dest = Drive.for_account(job["dest_account_id"])
        src = Drive.for_account(job["source_account_id"]) if job["mode"] == "account" else dest
        return src, dest

    # ------------------------------------------------------------------ SCAN
    def scan(self, job, jl: JobLog):
        job_id = job["id"]
        src, dest = self._clients(job)
        if not job["roots_ready"]:
            jl.write("INFO", f"=== BẮT ĐẦU JOB #{job_id}: {job['name']} ===")
            jl.write("INFO", "Chế độ: " + ("Link công khai -> tài khoản đích" if job["mode"] == "public"
                                           else "Tài khoản nguồn -> tài khoản đích"))
            roots = []
            if job["whole_drive"]:
                for f in src.list_my_drive_root():
                    roots.append((f, f.get("resourceKey"), None))
                jl.write("INFO", f"Toàn bộ My Drive nguồn: {len(roots)} mục ở cấp gốc")
            for raw in json.loads(job["inputs"]):
                ref = DriveRef(raw["id"], raw.get("resource_key"), raw.get("raw", raw["id"]))
                try:
                    meta = src.get(ref.id, ref.resource_key)
                    if meta.get("trashed"):
                        raise DriveError(404, "trashed", "File đã nằm trong thùng rác")
                    roots.append((meta, ref.resource_key or meta.get("resourceKey"), None))
                except DriveError as e:
                    roots.append(({"id": ref.id, "name": ref.raw, "mimeType": ""}, ref.resource_key, e.vi()))
                    jl.write("LỖI", f"Không đọc được link {ref.raw}: {e.vi()}")
            with db.tx() as c:
                for meta, rk, err in roots:
                    self._insert_item(c, job_id, 0, "", 0, meta, rk,
                                      status="failed" if err else None, error=err)
                c.execute("UPDATE jobs SET roots_ready=1 WHERE id=?", (job_id,))

        # Duyệt cây thư mục theo chiều rộng; mỗi thư mục liệt kê xong mới đánh dấu listed=1 (chạy tiếp được)
        last_report = 0.0
        while True:
            self._check_control(job_id)
            folders = db.q("SELECT * FROM items WHERE job_id=? AND is_folder=1 AND listed=0 AND status!='failed' "
                           "ORDER BY id LIMIT 20", (job_id,))
            if not folders:
                break
            for fo in folders:
                try:
                    children = list(src.list_children(fo["src_id"], fo["resource_key"]))
                except DriveError as e:
                    db.execute("UPDATE items SET status='failed', error=?, listed=1 WHERE id=?", (e.vi(), fo["id"]))
                    jl.write("LỖI", f"Không liệt kê được thư mục {fo['path']}: {e.vi()}")
                    continue
                with db.tx() as c:
                    for ch in children:
                        self._insert_item(c, job_id, fo["id"], fo["path"], fo["depth"] + 1, ch, ch.get("resourceKey"))
                    c.execute("UPDATE items SET listed=1 WHERE id=?", (fo["id"],))
            if time.time() - last_report > 5:
                s = db.job_stats(job_id)
                db.set_job(job_id, status_message=f"Đang quét: {s['folders_total']} thư mục, "
                                                  f"{s['files_total']} file, {human_bytes(s['bytes_total'])}")
                last_report = time.time()

        s = db.job_stats(job_id)
        jl.write("INFO", f"Quét xong: {s['folders_total']} thư mục, {s['files_total']} file, "
                         f"tổng {human_bytes(s['bytes_total'])}")
        self._decide_after_scan(job_id, dest, jl, s)

    def _decide_after_scan(self, job_id, dest: Drive, jl: JobLog, s: dict):
        job = db.get_job(job_id)
        limit, usage = quota_of(dest.about())
        need = db.q1("SELECT COALESCE(SUM(size),0) AS b FROM items WHERE job_id=? AND is_folder=0 AND status='pending'",
                     (job_id,))["b"]
        free = None if limit is None else max(0, limit - usage)
        # dest_free_bytes = -1 nghĩa là tài khoản đích không giới hạn dung lượng
        db.set_job(job_id, dest_free_bytes=-1 if free is None else free, dest_limit_bytes=limit)
        jl.write("INFO", f"Dung lượng đích: đã dùng {human_bytes(usage)} / {human_bytes(limit)}, "
                         f"còn trống {human_bytes(free)}; cần {human_bytes(need)}")
        if free is not None and need > free:
            msg = (f"CẢNH BÁO: Tài khoản đích KHÔNG ĐỦ dung lượng! Cần {human_bytes(need)} nhưng chỉ còn "
                   f"{human_bytes(free)} (thiếu {human_bytes(need - free)}). Hãy nâng cấp/giải phóng dung lượng "
                   f"rồi bấm 'Bắt đầu', hoặc bấm 'Vẫn chạy' để chuyển tới khi đầy.")
            jl.write("CẢNH BÁO", msg)
            db.set_job(job_id, status="awaiting_confirm", status_message=msg)
        elif job["auto_start"]:
            db.set_job(job_id, status="running", status_message="Đủ dung lượng, tự động bắt đầu chuyển")
        else:
            db.set_job(job_id, status="awaiting_confirm",
                       status_message="Quét xong, đủ dung lượng. Bấm 'Bắt đầu' để chuyển.")

    @staticmethod
    def _insert_item(c, job_id, parent_item_id, parent_path, depth, meta, rk, status=None, error=None):
        mime = meta.get("mimeType", "")
        name = meta.get("name") or meta["id"]
        is_folder = 1 if mime == FOLDER_MIME else 0
        if status is None:
            status = "pending"
            if mime == SHORTCUT_MIME:
                status, error = "skipped", "Shortcut (lối tắt) – không copy, hãy thêm link gốc nếu cần"
            elif mime in UNCOPYABLE_MIMES:
                status, error = "skipped", UNCOPYABLE_MIMES[mime]
        c.execute(
            """INSERT OR IGNORE INTO items(job_id, parent_item_id, src_id, resource_key, name, mime, size,
                   is_folder, path, depth, status, error, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, parent_item_id, meta["id"], rk, name, mime, int(meta.get("size") or 0), is_folder,
             f"{parent_path}/{name}", depth, status, error, db.now()))

    # ------------------------------------------------------------------ TRANSFER
    def transfer(self, job, jl: JobLog):
        job_id = job["id"]
        src, dest = self._clients(job)
        if not job["started_at"]:
            db.set_job(job_id, started_at=db.now())
        jl.write("INFO", "Bắt đầu/tiếp tục chuyển dữ liệu")

        root_id = job["dest_root_id"]
        if not root_id:
            marker = f"transload-job-{job_id}"
            parent = job["dest_parent_id"] or "root"
            root_id = dest.find_copied(marker, parent) or \
                dest.create_folder(job["dest_root_name"], job["dest_parent_id"], src_id=marker)
            db.set_job(job_id, dest_root_id=root_id)
            jl.write("INFO", f"Thư mục đích: {job['dest_root_name']} (https://drive.google.com/drive/folders/{root_id})")

        dest_email = None
        if job["mode"] == "account":
            dest_email = dest.about()["user"]["emailAddress"]
            self._share_roots(job_id, src, dest_email, jl)

        ctx = _TransferCtx(job, root_id, dest_email, jl)
        # 1) Tạo thư mục theo từng tầng
        max_depth = db.q1("SELECT COALESCE(MAX(depth),0) AS d FROM items WHERE job_id=? AND is_folder=1", (job_id,))["d"]
        for depth in range(max_depth + 1):
            while True:
                self._check_control(job_id)
                batch = db.q("SELECT * FROM items WHERE job_id=? AND is_folder=1 AND depth=? AND status='pending' "
                             "ORDER BY id LIMIT 200", (job_id, depth))
                if not batch:
                    break
                ctx.run_batch(batch, self._do_folder)

        # 2) Copy file
        while True:
            self._check_control(job_id)
            batch = db.q("SELECT * FROM items WHERE job_id=? AND is_folder=0 AND status='pending' "
                         "ORDER BY id LIMIT 500", (job_id,))
            if not batch:
                break
            ctx.run_batch(batch, self._do_file)
            s = db.job_stats(job_id)
            db.set_job(job_id, status_message=f"Đang chuyển: {s['files_done']}/{s['files_total']} file, "
                                              f"{human_bytes(s['bytes_done'])}/{human_bytes(s['bytes_total'])}")

        self._finish(job_id, src, jl)

    def _share_roots(self, job_id, src: Drive, email: str, jl: JobLog):
        roots = db.q("""SELECT i.src_id, i.path FROM items i
                        LEFT JOIN shared_perms p ON p.job_id=i.job_id AND p.src_id=i.src_id
                        WHERE i.job_id=? AND i.parent_item_id=0 AND i.status!='failed' AND p.src_id IS NULL""",
                     (job_id,))
        for r in roots:
            self._check_control(job_id)
            try:
                pid = src.share_with(r["src_id"], email)
                db.execute("INSERT OR IGNORE INTO shared_perms(job_id, src_id, perm_id) VALUES (?,?,?)",
                           (job_id, r["src_id"], pid))
            except DriveError as e:
                jl.write("CẢNH BÁO", f"Không chia sẻ được {r['path']} cho {email}: {e.vi()}")

    def _do_folder(self, ctx: "_TransferCtx", clients, item):
        _, dest = clients
        parent_dest = ctx.parent_dest_id(item)
        if parent_dest is None:
            return "failed", None, "Thư mục cha không tạo được"
        if item["maybe_done"]:
            existing = dest.find_copied(item["src_id"], parent_dest)
            if existing:
                return "done", existing, None
        return "done", dest.create_folder(item["name"], parent_dest, src_id=item["src_id"]), None

    def _do_file(self, ctx: "_TransferCtx", clients, item):
        src, dest = clients
        parent_dest = ctx.parent_dest_id(item)
        if parent_dest is None:
            return "failed", None, "Thư mục cha không tạo được"
        if item["maybe_done"]:
            existing = dest.find_copied(item["src_id"], parent_dest)
            if existing:
                return "done", existing, None
        try:
            return "done", dest.copy(item["src_id"], item["name"], parent_dest, item["resource_key"]), None
        except DriveError as e:
            # Chế độ tài khoản: nếu đích chưa thấy file (quyền không kế thừa), chia sẻ trực tiếp file rồi thử lại
            if ctx.dest_email and (e.is_not_found or e.status == 403) and not e.is_rate_limit and not e.is_quota_full:
                pid = src.share_with(item["src_id"], ctx.dest_email)
                db.execute("INSERT OR IGNORE INTO shared_perms(job_id, src_id, perm_id) VALUES (?,?,?)",
                           (ctx.job_id, item["src_id"], pid))
                return "done", dest.copy(item["src_id"], item["name"], parent_dest, item["resource_key"]), None
            raise

    def _finish(self, job_id, src: Drive, jl: JobLog):
        s = db.job_stats(job_id)
        job = db.get_job(job_id)
        if job["mode"] == "account":
            perms = db.q("SELECT * FROM shared_perms WHERE job_id=?", (job_id,))
            for p in perms:
                try:
                    src.delete_permission(p["src_id"], p["perm_id"])
                except DriveError:
                    pass
            db.execute("DELETE FROM shared_perms WHERE job_id=?", (job_id,))
            if perms:
                jl.write("INFO", f"Đã gỡ {len(perms)} quyền chia sẻ tạm thời ở tài khoản nguồn")

        failed = db.q("SELECT path, src_id, error, is_folder FROM items WHERE job_id=? AND status='failed' ORDER BY id",
                      (job_id,))
        skipped = db.q("SELECT path, src_id, error FROM items WHERE job_id=? AND status='skipped' ORDER BY id",
                       (job_id,))
        status = "completed" if not failed else "completed_with_errors"
        lines = [
            "",
            "=" * 70,
            f"KẾT QUẢ JOB #{job_id}: {job['name']}",
            f"Thời gian kết thúc : {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Thư mục đích       : https://drive.google.com/drive/folders/{job['dest_root_id']}",
            f"Chuyển xong        : {s['files_done']}/{s['files_total']} file ({human_bytes(s['bytes_done'])}), "
            f"{s['folders_done']}/{s['folders_total']} thư mục",
            f"Lỗi                : {len(failed)}",
            f"Bỏ qua             : {len(skipped)}",
        ]
        if failed:
            lines += ["", "--- DANH SÁCH LỖI (không chuyển được) ---"]
            lines += [f"[LỖI] {r['path']} | https://drive.google.com/open?id={r['src_id']} | {r['error']}" for r in failed]
        if skipped:
            lines += ["", "--- DANH SÁCH BỎ QUA ---"]
            lines += [f"[BỎ QUA] {r['path']} | https://drive.google.com/open?id={r['src_id']} | {r['error']}"
                      for r in skipped]
        lines.append("=" * 70)
        with open(jl.path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        msg = (f"Hoàn tất: {s['files_done']}/{s['files_total']} file, {human_bytes(s['bytes_done'])}"
               + (f", {len(failed)} lỗi" if failed else ""))
        db.set_job(job_id, status=status, status_message=msg, finished_at=db.now())


class _TransferCtx:
    """Chạy một lô item song song, gom lỗi giới hạn tốc độ / hết dung lượng để dừng job đúng cách."""

    def __init__(self, job, root_id: str, dest_email: str | None, jl: JobLog):
        self.job = job
        self.job_id = job["id"]
        self.root_id = root_id
        self.dest_email = dest_email
        self.jl = jl
        self.stop: StopJob | None = None
        self.rate_hits = 0
        self.lock = threading.Lock()
        self._local = threading.local()
        self._parent_cache: dict[int, str | None] = {}

    def clients(self):
        c = getattr(self._local, "clients", None)
        if c is None:
            dest = Drive.for_account(self.job["dest_account_id"])
            src = Drive.for_account(self.job["source_account_id"]) if self.job["mode"] == "account" else dest
            c = self._local.clients = (src, dest)
        return c

    def parent_dest_id(self, item) -> str | None:
        pid = item["parent_item_id"]
        if pid == 0:
            return self.root_id
        if pid not in self._parent_cache:
            row = db.q1("SELECT status, dest_id FROM items WHERE id=?", (pid,))
            self._parent_cache[pid] = row["dest_id"] if row and row["status"] == "done" else None
        return self._parent_cache[pid]

    def run_batch(self, batch, fn):
        self._parent_cache.clear()
        with ThreadPoolExecutor(max_workers=config.COPY_WORKERS) as pool:
            list(pool.map(lambda it: self._one(it, fn), batch))
        if self.stop:
            raise self.stop

    def _one(self, item, fn):
        if self.stop:
            return
        db.execute("UPDATE items SET status='in_progress', updated_at=? WHERE id=?", (db.now(), item["id"]))
        kind = "thư mục" if item["is_folder"] else "file"
        size = "" if item["is_folder"] else f" ({human_bytes(item['size'])})"
        try:
            status, dest_id, err = fn(self, self.clients(), item)
            with self.lock:
                self.rate_hits = 0
        except DriveError as e:
            status, dest_id, err = self._handle_error(item, e)
        except RefreshError as e:
            self.stop = StopJob("error", f"Phiên đăng nhập Google hết hạn/bị thu hồi: {e}. Hãy đăng nhập lại.")
            status, dest_id, err = "pending", None, None
        except Exception as e:  # lỗi mạng bất thường...
            status, dest_id, err = self._retry_or_fail(item, f"Lỗi: {e!r}")

        # pending sau lỗi (vd. timeout) -> có thể Google đã copy xong, lần sau kiểm tra trước để tránh trùng
        db.execute("UPDATE items SET status=?, dest_id=?, error=?, maybe_done=?, attempts=attempts+?, updated_at=? "
                   "WHERE id=?", (status, dest_id, err, 1 if status == "pending" else 0,
                                  0 if status == "done" or err is None else 1, db.now(), item["id"]))
        if status == "done":
            self.jl.write("OK", f"{kind} {item['path']}{size}")
        elif status == "failed":
            self.jl.write("LỖI", f"{kind} {item['path']}{size}: {err}")
        elif status == "skipped":
            self.jl.write("BỎ QUA", f"{kind} {item['path']}: {err}")

    def _handle_error(self, item, e: DriveError):
        if e.is_quota_full:
            self.stop = StopJob("paused_quota",
                                "Tài khoản đích đã HẾT dung lượng. Hãy nâng cấp/giải phóng dung lượng rồi bấm 'Tiếp tục'.")
            return "pending", None, None
        if e.is_rate_limit:
            with self.lock:
                self.rate_hits += 1
                hits = self.rate_hits
            if hits >= 3 and not self.stop:
                until = time.time() + config.THROTTLE_PAUSE_MINUTES * 60
                self.stop = StopJob(
                    "throttled",
                    f"Google đang giới hạn (thường do vượt 750GB/ngày hoặc quá nhiều yêu cầu). Tự động chạy lại lúc "
                    f"{time.strftime('%H:%M %d/%m', time.localtime(until))}. Chi tiết: {e.vi()}", until)
            else:
                time.sleep(20)
            return "pending", None, None
        if e.reason in PERMANENT_REASONS or e.is_not_found:
            return "failed", None, e.vi()
        return self._retry_or_fail(item, e.vi())

    @staticmethod
    def _retry_or_fail(item, err: str):
        if item["attempts"] + 1 >= config.MAX_ATTEMPTS:
            return "failed", None, err
        return "pending", None, err
