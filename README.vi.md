# Google Drive Transload

🌐 [English](README.md) | **Tiếng Việt**

> ☕ **Nếu thấy tool hữu ích, bạn có thể tặng tôi một cốc cafe** · *If you find this tool useful, you can buy me a coffee*
>
> **00004657001** – **TPBank (Tienphong Bank)** – **BUI MINH TUAN**

Tool web mã nguồn mở giúp **chuyển dữ liệu Google Drive nhanh chóng** từ tài khoản này sang tài khoản khác – kể cả hàng TB dữ liệu. **Triển khai miễn phí** trên máy cá nhân bằng **Docker**, hoặc chạy 24/7 trên **VPS**.

- **Nhanh & không tốn băng thông**: copy phía server Google (`files.copy`) – dữ liệu **không** phải tải về rồi upload lại, nên dù nhiều TB cũng không tốn băng thông, VPS cấu hình thấp nhất cũng chạy được.
- **2 chế độ**
  - **A. Chỉ có link chia sẻ công khai** → chỉ đăng nhập tài khoản **đích**.
  - **B. Đăng nhập được tài khoản nguồn** → đăng nhập **nguồn + đích**; có thể chuyển **toàn bộ My Drive**. Tool tự chia sẻ tạm cho tài khoản đích, copy xong tự gỡ quyền.
- Nhập link: paste 1 link, nhiều link (mỗi dòng 1 link) hoặc **import file Excel** (`.xlsx`, `.xls`, `.csv`, `.txt` – đọc cả hyperlink ẩn trong ô).
- Giữ nguyên **cấu trúc thư mục**.
- **Tự phát hiện & cảnh báo** khi tài khoản đích **không đủ dung lượng** (kiểm tra sau khi quét, và dừng an toàn nếu đầy giữa chừng).
- **Chạy nền**, tự **chạy tiếp** sau khi container/VPS khởi động lại (trạng thái lưu SQLite); tự nghỉ và chạy lại khi chạm **giới hạn 750GB/ngày** của Google.
- **Log .txt** cho từng job: `data/logs/job_<id>.txt` – từng file `[OK]` / `[LỖI]` / `[BỎ QUA]` + bảng tổng kết và danh sách lỗi ở cuối.

---

## 1. Tạo Google OAuth Client (làm 1 lần, ~10 phút)

1. Vào <https://console.cloud.google.com/> → tạo Project mới (vd. `drive-transload`).
2. **APIs & Services → Library** → tìm **Google Drive API** → **Enable**.
3. **APIs & Services → OAuth consent screen** (Google Auth Platform):
   - User type: **External** → điền tên app, email.
   - **Data access / Scopes**: thêm `https://www.googleapis.com/auth/drive`.
   - **Audience / Test users**: thêm email tài khoản nguồn và tài khoản đích.
   - ⚠️ **Quan trọng:** bấm **Publish app** (chuyển sang *In production*). Nếu để *Testing*, refresh token **hết hạn sau 7 ngày** – job lớn (nhiều TB, kéo dài nhiều ngày) có thể bị dừng giữa chừng. App chưa xác minh vẫn dùng được cho chính bạn; khi đăng nhập sẽ thấy cảnh báo *"Google hasn't verified this app"* → bấm **Advanced → Go to … (unsafe)**.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**
   - Application type: **Web application**
   - **Authorized redirect URIs**: `http://localhost:8080/oauth/callback`
     (nếu dùng domain riêng: `https://ten-mien-cua-ban/oauth/callback`)
5. Copy **Client ID** và **Client secret**.

## 2. Chạy trên máy (Docker Desktop)

```bash
git clone https://github.com/tuannhh/Google-Drive-transload.git
cd Google-Drive-transload
cp .env.example .env        # rồi sửa GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
docker compose up -d --build
```

Mở <http://localhost:8080> →

1. **Đăng nhập tài khoản Google** (đích; chế độ B thì đăng nhập thêm tài khoản nguồn).
2. Chọn chế độ A/B, chọn tài khoản, paste link hoặc import Excel → **Tạo job & bắt đầu quét**.
3. Tool quét toàn bộ cây thư mục, tính tổng dung lượng, so với dung lượng trống ở đích:
   - Đủ → tự bắt đầu chuyển (nếu bật *Tự động bắt đầu*).
   - Không đủ → hiện **CẢNH BÁO** đỏ, chờ bạn nâng cấp dung lượng rồi bấm *Kiểm tra lại dung lượng*, hoặc *Vẫn chạy* (chuyển tới khi đầy).
4. Theo dõi tiến độ, xem/tải **log .txt**, *Thử lại mục lỗi*, *Tạm dừng/Tiếp tục/Huỷ*.

> Sau khi đăng nhập xong có thể đóng trình duyệt – job vẫn chạy trong container.

## 3. Chạy 24/7 kể cả khi TẮT MÁY TÍNH (khuyến nghị cho dữ liệu lớn)

Docker trên máy cá nhân sẽ dừng khi tắt máy (khi bật lại, job **tự chạy tiếp**). Muốn chạy liên tục khi tắt máy, chạy cùng Docker Compose này trên **VPS** (1 vCPU/1GB RAM là đủ, vì dữ liệu được copy phía Google):

```bash
# Trên VPS (Ubuntu)
curl -fsSL https://get.docker.com | sh
git clone https://github.com/tuannhh/Google-Drive-transload.git && cd Google-Drive-transload
cp .env.example .env && nano .env      # điền Client ID/Secret, đặt APP_PASSWORD
docker compose up -d --build
```

Đăng nhập Google qua **SSH tunnel** (không cần domain/HTTPS, vì Google chỉ cho phép redirect `http://` với `localhost`):

```bash
ssh -L 8080:localhost:8080 user@IP_VPS
```

Rồi mở <http://localhost:8080> trên máy bạn, đăng nhập, tạo job. Xong có thể đóng SSH và **tắt máy** – VPS tiếp tục chạy. Mở lại tunnel bất cứ lúc nào để xem tiến độ.

Nếu có domain + HTTPS (vd. qua Caddy/Nginx): đặt `BASE_URL=https://ten-mien` và thêm redirect URI tương ứng trong Google Cloud. **Nhớ đặt `APP_PASSWORD`.**

## 4. Giới hạn cần biết

| Vấn đề | Tool xử lý |
|---|---|
| Google giới hạn ~**750GB copy/ngày**/tài khoản → 1TB mất khoảng 1,5 ngày | Tự phát hiện, nghỉ `THROTTLE_PAUSE_MINUTES` (mặc định 60) rồi tự chạy tiếp |
| File chủ sở hữu **chặn tải xuống/sao chép** | Chế độ A: ghi `[LỖI]`. Chế độ B: tự chia sẻ quyền Editor rồi copy được |
| Google Sites, My Maps | Không copy được qua API → `[BỎ QUA]` |
| Shortcut (lối tắt) | `[BỎ QUA]` – thêm link gốc nếu cần |
| Đích hết dung lượng giữa chừng | Dừng an toàn (`Dừng – hết dung lượng`), bấm *Tiếp tục* sau khi nâng cấp |
| Container/VPS khởi động lại | Tự chạy tiếp, kiểm tra trùng bằng `appProperties` nên không copy lặp |
| Bản copy | Thuộc sở hữu tài khoản đích; không mang theo lịch sử phiên bản, bình luận, quyền chia sẻ cũ |

## 5. File log

`data/logs/job_<id>.txt` (tải trên giao diện bằng nút **⬇ Log .txt**):

```
[2026-09-28 09:00:01] [INFO] === BẮT ĐẦU JOB #1: 3 link – 2026-09-28 0900 ===
[2026-09-28 09:02:10] [INFO] Quét xong: 120 thư mục, 8 500 file, tổng 1.20 TB
[2026-09-28 09:02:11] [INFO] Dung lượng đích: đã dùng 15.00 GB / 2.00 TB, còn trống 1.99 TB; cần 1.20 TB
[2026-09-28 09:02:15] [OK] file /Ảnh/2019/IMG_0001.JPG (4.20 MB)
[2026-09-28 09:02:16] [LỖI] file /Tài liệu/khoa.pdf (2.00 MB): Chủ sở hữu đã chặn tải xuống/sao chép file này (403 cannotCopyFile)
[2026-09-28 13:40:00] [TẠM NGHỈ] Google đang giới hạn (thường do vượt 750GB/ngày ...). Tự động chạy lại lúc 14:40 28/09
...
======================================================================
KẾT QUẢ JOB #1: ...
Chuyển xong        : 8490/8500 file (1.19 TB), 120/120 thư mục
Lỗi                : 7
Bỏ qua             : 3
--- DANH SÁCH LỖI (không chuyển được) ---
[LỖI] /Tài liệu/khoa.pdf | https://drive.google.com/open?id=... | Chủ sở hữu đã chặn ...
```

## 6. Cấu hình `.env`

| Biến | Ý nghĩa |
|---|---|
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | OAuth client (bước 1) |
| `BASE_URL` | URL mở tool; redirect URI = `BASE_URL/oauth/callback` |
| `APP_USERNAME`, `APP_PASSWORD` | Bảo vệ giao diện (Basic Auth). Để trống = không khoá |
| `PORT` | Cổng publish ra ngoài (mặc định 8080) |
| `COPY_WORKERS` | Số file copy song song (mặc định 4) |
| `THROTTLE_PAUSE_MINUTES` | Thời gian nghỉ khi bị Google giới hạn |
| `MAX_ATTEMPTS` | Số lần thử lại mỗi file khi lỗi tạm thời |

## 7. Phát triển

```bash
docker build -t gdrive-transload:dev .
docker run --rm gdrive-transload:dev python -m pytest -q tests
```

Cấu trúc: `app/main.py` (FastAPI + OAuth + API), `app/worker.py` (worker nền), `app/drive.py` (Drive API), `app/links.py` (tách link/Excel), `app/db.py` (SQLite), `app/static/index.html` (giao diện). Tiến độ phát triển & ghi chú cho AI agent: [`memory-bank/`](memory-bank/).

## ☕ Ủng hộ

Nếu tool giúp bạn tiết kiệm thời gian, hãy tặng tôi một cốc cafe nhé:

**00004657001** – **TPBank (Tienphong Bank)** – **BUI MINH TUAN**

Cảm ơn bạn! ❤️
