import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "transload.db"

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
# URL mà trình duyệt dùng để mở tool. Redirect URI phải khai báo trong Google Cloud:
#   {BASE_URL}/oauth/callback
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/")

# Bảo vệ giao diện web bằng HTTP Basic Auth (nên đặt khi chạy trên VPS)
APP_USERNAME = os.environ.get("APP_USERNAME", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

# Số luồng copy song song cho mỗi job
COPY_WORKERS = int(os.environ.get("COPY_WORKERS", "4"))
# Khi bị Google giới hạn (thường là 750GB/ngày), nghỉ bao nhiêu phút rồi thử lại
THROTTLE_PAUSE_MINUTES = int(os.environ.get("THROTTLE_PAUSE_MINUTES", "60"))
# Số lần thử lại tối đa cho một file bị lỗi tạm thời
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "5"))

TZ_NAME = os.environ.get("TZ", "Asia/Ho_Chi_Minh")

SCOPES = ["https://www.googleapis.com/auth/drive"]


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
