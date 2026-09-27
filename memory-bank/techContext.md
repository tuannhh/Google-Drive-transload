# Tech context

- Python 3.12, FastAPI + Uvicorn (**1 worker process** – bắt buộc, vì worker nền chạy trong process).
- Gọi Drive API v3 trực tiếp bằng `requests` + `google.auth.transport.requests.AuthorizedSession`
  (KHÔNG dùng google-api-python-client vì httplib2 không thread-safe).
- OAuth làm thủ công (không dùng google-auth-oauthlib): `drive.auth_url()` / `drive.exchange_code()`;
  scope `https://www.googleapis.com/auth/drive`, `access_type=offline`, `prompt=consent` để luôn có refresh token.
  Chỉ lưu refresh token trong SQLite.
- SQLite (WAL) tại `/data/transload.db`; log job tại `/data/logs/job_<id>.txt`. Volume `./data:/data`.
- openpyxl (.xlsx), xlrd (.xls), csv.
- Giao diện: 1 file `app/static/index.html` vanilla JS, poll `/api/jobs` mỗi 4s.

## Cấu trúc
```
app/config.py   biến môi trường
app/db.py       schema + helper (accounts, jobs, items, shared_perms), job_stats()
app/links.py    parse_link / parse_many / extract_from_file
app/drive.py    Drive client, DriveError (+ thông báo tiếng Việt), OAuth helpers
app/worker.py   Worker thread: scan -> quota check -> tạo folder theo depth -> copy file song song
app/main.py     FastAPI routes, Basic Auth middleware, OAuth callback
tests/          pytest (FakeDrive giả lập Drive)
```

## Chạy & test
```
cp .env.example .env   # điền GOOGLE_CLIENT_ID/SECRET
docker compose up -d --build           # http://localhost:8080
docker build -t gdrive-transload:dev . && docker run --rm gdrive-transload:dev python -m pytest -q tests
```
Máy dev Windows của người dùng: Docker Desktop ở `%LOCALAPPDATA%\Programs\DockerDesktop`, không có `gh` CLI.
