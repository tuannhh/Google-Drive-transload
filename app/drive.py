"""Client Google Drive API v3 tối giản (requests + google-auth), an toàn khi mỗi luồng tự tạo client riêng."""
import json
import random
import time
from urllib.parse import urlencode

import requests
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

from . import config, db

API = "https://www.googleapis.com/drive/v3"
TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

# Loại file Google không cho phép copy qua API
UNCOPYABLE_MIMES = {
    "application/vnd.google-apps.site": "Google Sites không hỗ trợ copy qua API",
    "application/vnd.google-apps.map": "Google My Maps không hỗ trợ copy qua API",
    "application/vnd.google-apps.fusiontable": "Fusion Tables đã ngừng hoạt động",
}

RATE_REASONS = {"userRateLimitExceeded", "rateLimitExceeded", "dailyLimitExceeded",
                "sharingRateLimitExceeded", "backendError", "internalError"}

VI_REASONS = {
    "cannotCopyFile": "Chủ sở hữu đã chặn tải xuống/sao chép file này",
    "notFound": "Không tìm thấy hoặc không có quyền truy cập (link chưa chia sẻ công khai?)",
    "storageQuotaExceeded": "Tài khoản đích đã HẾT dung lượng",
    "quotaExceeded": "Tài khoản đích đã HẾT dung lượng",
    "insufficientFilePermissions": "Không đủ quyền với file",
    "fileNotDownloadable": "File không cho phép tải/sao chép",
    "userRateLimitExceeded": "Vượt giới hạn tốc độ/giới hạn 750GB mỗi ngày của Google",
    "rateLimitExceeded": "Vượt giới hạn tốc độ của Google",
    "dailyLimitExceeded": "Vượt giới hạn hằng ngày của Google",
    "activeItemCreationLimitExceeded": "Tài khoản đích vượt giới hạn số lượng file Google cho phép",
    "teamDriveFileLimitExceeded": "Shared drive đích vượt giới hạn số lượng file",
}


class DriveError(Exception):
    def __init__(self, status: int, reason: str, message: str):
        self.status = status
        self.reason = reason or ""
        self.message = message or ""
        super().__init__(f"{status} {reason}: {message}")

    @property
    def is_rate_limit(self) -> bool:
        return self.status == 429 or self.reason in RATE_REASONS or self.status >= 500

    @property
    def is_quota_full(self) -> bool:
        return self.reason in ("storageQuotaExceeded", "quotaExceeded") or \
            "storage quota" in self.message.lower()

    @property
    def is_not_found(self) -> bool:
        return self.status == 404 or self.reason == "notFound"

    def vi(self) -> str:
        base = VI_REASONS.get(self.reason)
        return f"{base} ({self.status} {self.reason})" if base else f"{self.status} {self.reason}: {self.message}"


# ---------------- OAuth ----------------

def auth_url(state: str) -> str:
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": f"{config.BASE_URL}/oauth/callback",
        "response_type": "code",
        "scope": " ".join(config.SCOPES),
        "access_type": "offline",
        "prompt": "consent select_account",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{AUTH_URI}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    r = requests.post(TOKEN_URI, data={
        "code": code,
        "client_id": config.GOOGLE_CLIENT_ID,
        "client_secret": config.GOOGLE_CLIENT_SECRET,
        "redirect_uri": f"{config.BASE_URL}/oauth/callback",
        "grant_type": "authorization_code",
    }, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Đổi mã OAuth thất bại: {r.text}")
    return r.json()


def credentials_from_refresh(refresh_token: str) -> Credentials:
    return Credentials(
        token=None, refresh_token=refresh_token, token_uri=TOKEN_URI,
        client_id=config.GOOGLE_CLIENT_ID, client_secret=config.GOOGLE_CLIENT_SECRET,
        scopes=config.SCOPES)


# ---------------- Client ----------------

class Drive:
    LIST_FIELDS = "nextPageToken, files(id, name, mimeType, size, resourceKey, trashed, shortcutDetails, capabilities/canCopy)"
    FILE_FIELDS = "id, name, mimeType, size, resourceKey, trashed, parents, capabilities/canCopy, owners/emailAddress"

    def __init__(self, refresh_token: str):
        self.creds = credentials_from_refresh(refresh_token)
        self.creds.refresh(Request())
        self.session = AuthorizedSession(self.creds)

    @classmethod
    def for_account(cls, account_id: int) -> "Drive":
        acc = db.get_account(account_id)
        if not acc:
            raise RuntimeError(f"Không tìm thấy tài khoản #{account_id} (đã bị xoá?)")
        return cls(acc["refresh_token"])

    # -- low level --
    def _call(self, method: str, path: str, *, params=None, body=None, resource_keys=None,
              timeout=(15, 120), retries=5):
        headers = {}
        if resource_keys:
            headers["X-Goog-Drive-Resource-Keys"] = ",".join(f"{i}/{k}" for i, k in resource_keys if k)
        params = dict(params or {})
        params.setdefault("supportsAllDrives", "true")
        attempt = 0
        while True:
            attempt += 1
            try:
                r = self.session.request(method, f"{API}{path}", params=params,
                                         data=json.dumps(body) if body is not None else None,
                                         headers={**headers, "Content-Type": "application/json"} if body is not None else headers,
                                         timeout=timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt >= retries:
                    raise DriveError(0, "network", str(e))
                time.sleep(min(60, 2 ** attempt) + random.random())
                continue
            if r.status_code < 300:
                return r.json() if r.content else {}
            err = _parse_error(r)
            # Chỉ tự thử lại ngắn hạn ở đây; giới hạn dài hạn (750GB/ngày) do worker xử lý
            if err.is_rate_limit and attempt < retries:
                time.sleep(min(64, 2 ** attempt) + random.random())
                continue
            raise err

    # -- API --
    def about(self) -> dict:
        return self._call("GET", "/about", params={"fields": "user(emailAddress,displayName),storageQuota"})

    def get(self, file_id: str, resource_key: str | None = None) -> dict:
        return self._call("GET", f"/files/{file_id}", params={"fields": self.FILE_FIELDS},
                          resource_keys=[(file_id, resource_key)] if resource_key else None)

    def list_children(self, folder_id: str, resource_key: str | None = None):
        token = None
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": self.LIST_FIELDS,
                "pageSize": 1000,
                "includeItemsFromAllDrives": "true",
            }
            if token:
                params["pageToken"] = token
            res = self._call("GET", "/files", params=params,
                             resource_keys=[(folder_id, resource_key)] if resource_key else None)
            for f in res.get("files", []):
                yield f
            token = res.get("nextPageToken")
            if not token:
                return

    def list_my_drive_root(self):
        """Các item ở cấp gốc My Drive mà tài khoản sở hữu."""
        token = None
        while True:
            params = {"q": "'root' in parents and trashed = false and 'me' in owners",
                      "fields": self.LIST_FIELDS, "pageSize": 1000}
            if token:
                params["pageToken"] = token
            res = self._call("GET", "/files", params=params)
            yield from res.get("files", [])
            token = res.get("nextPageToken")
            if not token:
                return

    def create_folder(self, name: str, parent_id: str | None, src_id: str | None = None) -> str:
        body = {"name": name, "mimeType": FOLDER_MIME}
        if parent_id:
            body["parents"] = [parent_id]
        if src_id:
            body["appProperties"] = {"transloadSrc": src_id}
        return self._call("POST", "/files", params={"fields": "id"}, body=body)["id"]

    def copy(self, src_id: str, name: str, parent_id: str, resource_key: str | None = None) -> str:
        body = {"name": name, "parents": [parent_id], "appProperties": {"transloadSrc": src_id}}
        # Copy phía server có thể mất vài phút với file rất lớn
        res = self._call("POST", f"/files/{src_id}/copy", params={"fields": "id"}, body=body,
                         resource_keys=[(src_id, resource_key)] if resource_key else None,
                         timeout=(15, 1800), retries=3)
        return res["id"]

    def find_copied(self, src_id: str, parent_id: str) -> str | None:
        """Tìm bản copy đã tạo trước đó (dùng khi chạy tiếp sau sự cố, tránh copy trùng)."""
        q = (f"'{parent_id}' in parents and trashed = false and "
             f"appProperties has {{ key='transloadSrc' and value='{src_id}' }}")
        res = self._call("GET", "/files", params={"q": q, "fields": "files(id)", "pageSize": 1})
        files = res.get("files", [])
        return files[0]["id"] if files else None

    def share_with(self, file_id: str, email: str, role: str = "writer") -> str:
        res = self._call("POST", f"/files/{file_id}/permissions",
                         params={"sendNotificationEmail": "false", "fields": "id"},
                         body={"type": "user", "role": role, "emailAddress": email})
        return res["id"]

    def delete_permission(self, file_id: str, perm_id: str) -> None:
        self._call("DELETE", f"/files/{file_id}/permissions/{perm_id}")


def _parse_error(r: requests.Response) -> DriveError:
    reason, message = "", r.text[:500]
    try:
        err = r.json().get("error", {})
        message = err.get("message", message)
        errs = err.get("errors") or []
        if errs:
            reason = errs[0].get("reason", "")
        if not reason:
            reason = err.get("status", "")
    except ValueError:
        pass
    return DriveError(r.status_code, reason, message)


def quota_of(about: dict) -> tuple[int | None, int]:
    """(limit, usage) theo byte. limit=None nghĩa là không giới hạn."""
    sq = about.get("storageQuota", {})
    limit = int(sq["limit"]) if sq.get("limit") else None
    usage = int(sq.get("usage", 0))
    return limit, usage
