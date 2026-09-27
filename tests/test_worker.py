"""Chạy worker với một Drive giả lập để kiểm tra luồng quét -> kiểm tra dung lượng -> copy -> log."""
import itertools
import json

import pytest

from app import config, db
from app.drive import FOLDER_MIME, SHORTCUT_MIME, DriveError

GB = 1024 ** 3


class FakeDrive:
    """Cây nguồn:  ROOT/ {a.bin 1GB, locked.bin, sub/ {b.bin 2GB, link(shortcut)}}"""
    files = {}
    children = {}
    created = []
    copied = []
    limit = 100 * GB
    usage = 0
    rate_limit_left = 0
    _ids = itertools.count(1)

    @classmethod
    def reset(cls):
        cls.files = {
            "ROOT": {"id": "ROOT", "name": "Du lieu", "mimeType": FOLDER_MIME},
            "A": {"id": "A", "name": "a.bin", "mimeType": "application/octet-stream", "size": str(GB)},
            "L": {"id": "L", "name": "locked.bin", "mimeType": "application/octet-stream", "size": "10"},
            "SUB": {"id": "SUB", "name": "sub", "mimeType": FOLDER_MIME},
            "B": {"id": "B", "name": "b.bin", "mimeType": "application/octet-stream", "size": str(2 * GB)},
            "S": {"id": "S", "name": "link", "mimeType": SHORTCUT_MIME},
        }
        cls.children = {"ROOT": ["A", "L", "SUB"], "SUB": ["B", "S"]}
        cls.created, cls.copied = [], []
        cls.limit, cls.usage, cls.rate_limit_left = 100 * GB, 0, 0

    def about(self):
        return {"user": {"emailAddress": "dest@example.com", "displayName": "Dest"},
                "storageQuota": {"limit": str(self.limit), "usage": str(self.usage)}}

    def get(self, fid, rk=None):
        if fid not in self.files:
            raise DriveError(404, "notFound", "File not found")
        return self.files[fid]

    def list_children(self, fid, rk=None):
        return [self.files[c] for c in self.children.get(fid, [])]

    def list_my_drive_root(self):
        return [self.files["ROOT"]]

    def create_folder(self, name, parent, src_id=None):
        nid = f"D{next(self._ids)}"
        self.created.append((nid, name, parent))
        return nid

    def copy(self, src_id, name, parent, rk=None):
        if FakeDrive.rate_limit_left > 0:
            FakeDrive.rate_limit_left -= 1
            raise DriveError(403, "userRateLimitExceeded", "User rate limit exceeded")
        if src_id == "L":
            raise DriveError(403, "cannotCopyFile", "This file cannot be copied")
        nid = f"C{next(self._ids)}"
        self.copied.append((src_id, name, parent))
        return nid

    def find_copied(self, src_id, parent):
        return None

    def share_with(self, fid, email, role="writer"):
        return "perm1"

    def delete_permission(self, fid, pid):
        pass


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "COPY_WORKERS", 2)
    db._local.conn = None
    db.init()
    from app import worker as wmod
    monkeypatch.setattr(wmod.Drive, "for_account", classmethod(lambda cls, _id: FakeDrive()))
    monkeypatch.setattr(wmod.time, "sleep", lambda s: None)
    FakeDrive.reset()
    acc = db.upsert_account("dest@example.com", "Dest", "rt")
    yield wmod, acc
    db._local.conn.close()
    db._local.conn = None


def _job(acc, auto_start=1):
    with db.tx() as c:
        return c.execute(
            "INSERT INTO jobs(name, mode, dest_account_id, inputs, dest_root_name, auto_start, status, created_at) "
            "VALUES ('t','public',?,?, 'Transload', ?, 'scanning', 0)",
            (acc, json.dumps([{"id": "ROOT", "raw": "link1"}, {"id": "MISSING", "raw": "link2"}]), auto_start)).lastrowid


def test_full_flow(env):
    wmod, acc = env
    jid = _job(acc)
    wmod.Worker().process(jid)
    job = db.get_job(jid)
    assert job["status"] == "completed_with_errors"
    s = db.job_stats(jid)
    assert s["files_done"] == 2 and s["bytes_done"] == 3 * GB
    assert s["files_failed"] == 2  # locked.bin + link không tồn tại
    assert s["files_skipped"] == 1  # shortcut
    assert {c[0] for c in FakeDrive.copied} == {"A", "B"}
    # b.bin phải nằm trong thư mục "sub" đã tạo ở đích
    sub_dest = next(n for n, name, _ in FakeDrive.created if name == "sub")
    assert ("B", "b.bin", sub_dest) in FakeDrive.copied
    log = (config.LOG_DIR / f"job_{jid}.txt").read_text(encoding="utf-8")
    assert "[OK] file /Du lieu/a.bin" in log
    assert "KẾT QUẢ JOB" in log and "locked.bin" in log and "Chủ sở hữu đã chặn" in log


def test_insufficient_quota_warns(env):
    wmod, acc = env
    FakeDrive.limit, FakeDrive.usage = 2 * GB, 0
    jid = _job(acc)
    wmod.Worker().process(jid)
    job = db.get_job(jid)
    assert job["status"] == "awaiting_confirm"
    assert "KHÔNG ĐỦ" in job["status_message"]
    assert FakeDrive.copied == []


def test_throttle_then_resume(env):
    wmod, acc = env
    FakeDrive.rate_limit_left = 3
    jid = _job(acc)
    w = wmod.Worker()
    w.process(jid)
    job = db.get_job(jid)
    assert job["status"] == "throttled" and job["wait_until"]
    db.set_job(jid, wait_until=0)
    assert w._next_job()["id"] == jid
    w.process(jid)
    assert db.get_job(jid)["status"] == "completed_with_errors"
    assert db.job_stats(jid)["files_done"] == 2
