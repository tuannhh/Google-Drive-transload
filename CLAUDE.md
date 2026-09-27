# Hướng dẫn cho AI agent

Dự án: **Google Drive Transload** – tool Docker chuyển dữ liệu giữa 2 tài khoản Google Drive (copy phía server).

**Bắt đầu mỗi phiên:** đọc toàn bộ `memory-bank/` theo thứ tự:
`projectbrief.md` → `techContext.md` → `systemPatterns.md` → `progress.md` → `activeContext.md`.

**Kết thúc mỗi phiên / sau thay đổi đáng kể:** cập nhật `memory-bank/activeContext.md` và `memory-bank/progress.md`
(ghi ngày tuyệt đối), chạy test, commit và push lên GitHub (`origin` = github.com/tuannhh/Google-Drive-transload).

Quy ước: giao diện, log, thông báo lỗi và tài liệu viết bằng **tiếng Việt**. Test chạy trong Docker:
`docker build -t gdrive-transload:dev . && docker run --rm gdrive-transload:dev python -m pytest -q tests`.
Không commit `.env` hay thư mục `data/` (chứa refresh token).
