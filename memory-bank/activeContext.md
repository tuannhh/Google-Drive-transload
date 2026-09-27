# Active context

**Cập nhật lần cuối:** 2026-09-27 (phiên đầu tiên – dựng v0.1)

## Trạng thái hiện tại
v0.1 hoàn chỉnh, test tự động pass, đã chạy thử bằng Docker Desktop trên máy Windows của người dùng
(thư mục `C:\Users\<user-name>\google-drive-transload`).
2026-09-27: người dùng đã tạo OAuth client (Web app, redirect `http://localhost:8080/oauth/callback`) và điền vào
`.env` trên máy Windows (KHÔNG commit). Đã kiểm tra `/oauth/start` → Google chấp nhận client_id + redirect_uri
(tới trang đăng nhập, không lỗi). Chưa đăng nhập tài khoản thật / chưa chạy job thật.
Máy khác: phải tự tạo `.env` từ `.env.example` và điền lại Client ID/Secret (lấy từ Google Cloud Console).

## Việc tiếp theo cho người dùng
1. Tạo OAuth client theo README mục 1 (nhớ **Publish app** để refresh token không hết hạn sau 7 ngày).
2. Điền `.env`, `docker compose up -d --build`, đăng nhập, chạy thử job nhỏ trước.
3. Muốn chạy khi tắt máy → deploy lên VPS (README mục 3).

## Việc tiếp theo cho AI agent
- Khi người dùng báo lỗi thật từ Google: xem `data/logs/job_<id>.txt` và `docker compose logs`,
  cập nhật `drive.py` (RATE_REASONS / VI_REASONS) và `worker.py` (PERMANENT_REASONS) cho đúng reason thực tế.
- Sau mỗi phiên: cập nhật `activeContext.md` + `progress.md`, commit & push.
