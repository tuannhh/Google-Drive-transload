"""Tách ID (và resourcekey) từ link Google Drive; đọc link từ file Excel/CSV."""
import csv
import io
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

ID_RE = r"[A-Za-z0-9_-]{10,}"

_PATH_PATTERNS = [
    re.compile(rf"/(?:file|document|spreadsheets|presentation|forms|drawings|uc)/(?:u/\d+/)?d/(?:e/)?({ID_RE})"),
    re.compile(rf"/folders/({ID_RE})"),
    re.compile(rf"/d/({ID_RE})"),
]
_BARE_ID = re.compile(rf"^{ID_RE}$")
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>]+", re.I)


@dataclass(frozen=True)
class DriveRef:
    id: str
    resource_key: str | None = None
    raw: str = ""


def parse_link(text: str) -> DriveRef | None:
    s = (text or "").strip().strip("<>\"'")
    if not s:
        return None
    if _BARE_ID.match(s) and len(s) >= 19:
        return DriveRef(s, None, s)
    if not s.lower().startswith("http"):
        s = "https://" + s
    try:
        u = urlparse(s)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if not (host.endswith("drive.google.com") or host.endswith("docs.google.com")
            or host.endswith("drive.usercontent.google.com")):
        return None
    qs = parse_qs(u.query)
    rk = (qs.get("resourcekey") or qs.get("resourceKey") or [None])[0]
    for pat in _PATH_PATTERNS:
        m = pat.search(u.path)
        if m:
            return DriveRef(m.group(1), rk, text.strip())
    if qs.get("id"):
        fid = qs["id"][0]
        if re.fullmatch(ID_RE, fid):
            return DriveRef(fid, rk, text.strip())
    return None


def parse_many(text: str) -> tuple[list[DriveRef], list[str]]:
    """Trả về (danh sách ref hợp lệ, danh sách dòng không nhận dạng được)."""
    refs: list[DriveRef] = []
    bad: list[str] = []
    seen: set[str] = set()
    for line in re.split(r"[\r\n]+", text or ""):
        line = line.strip()
        if not line:
            continue
        candidates = _URL_IN_TEXT.findall(line) or re.split(r"[\s,;]+", line)
        found = False
        for c in candidates:
            ref = parse_link(c)
            if ref:
                found = True
                if ref.id not in seen:
                    seen.add(ref.id)
                    refs.append(ref)
        if not found:
            bad.append(line)
    return refs, bad


def extract_from_file(filename: str, data: bytes) -> list[str]:
    """Lấy tất cả link Drive trong mọi ô (kể cả hyperlink ẩn) của file xlsx/xls/csv/txt."""
    name = filename.lower()
    texts: list[str] = []
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=False, data_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if cell.hyperlink is not None and cell.hyperlink.target:
                        texts.append(cell.hyperlink.target)
                    if cell.value is not None:
                        texts.append(str(cell.value))
    elif name.endswith(".xls"):
        import xlrd
        book = xlrd.open_workbook(file_contents=data)
        for sh in book.sheets():
            for link in getattr(sh, "hyperlink_list", []) or []:
                if link.url_or_path:
                    texts.append(link.url_or_path)
            for r in range(sh.nrows):
                for v in sh.row_values(r):
                    if v not in (None, ""):
                        texts.append(str(v))
    else:  # csv / txt
        content = data.decode("utf-8-sig", errors="ignore")
        if name.endswith(".csv"):
            for row in csv.reader(io.StringIO(content)):
                texts.extend(row)
        else:
            texts.extend(content.splitlines())

    out: list[str] = []
    for t in texts:
        # Chỉ nhận URL (không nhận ID trần) để tránh nhầm với dữ liệu khác trong bảng tính
        candidates = _URL_IN_TEXT.findall(t) or re.findall(r"(?:drive|docs)\.google\.com/\S+", t)
        out.extend(u for u in candidates if parse_link(u))
    return list(dict.fromkeys(out))
