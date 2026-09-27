# Progress

## Đã xong (v0.1 – 2026-09-27)
- [x] Docker + docker-compose (restart unless-stopped, volume ./data), healthcheck `/healthz`.
- [x] OAuth Google nhiều tài khoản, hiển thị dung lượng từng tài khoản.
- [x] Chế độ A (link công khai → đích) và B (nguồn + đích, tuỳ chọn toàn bộ My Drive).
- [x] Nhập link: textarea nhiều dòng, import .xlsx/.xls/.csv/.txt (đọc cả hyperlink ẩn), nút "Kiểm tra link".
- [x] Quét đệ quy giữ cấu trúc thư mục, resumable.
- [x] Cảnh báo thiếu dung lượng sau quét; dừng an toàn khi đầy giữa chừng.
- [x] Copy song song, chống trùng khi chạy lại, tự nghỉ khi bị giới hạn 750GB/ngày.
- [x] Log `.txt` mỗi job + tổng kết + danh sách lỗi/bỏ qua; xem tail & tải trên UI.
- [x] Pause / Resume / Cancel / Retry failed / Xoá job.
- [x] Basic Auth tuỳ chọn (APP_PASSWORD).
- [x] README song ngữ: `README.md` (English, mặc định trên GitHub) + `README.vi.md` (Tiếng Việt), có khối donate ở đầu và cuối (2026-09-27).
- [x] 9 test pytest (links, Excel, luồng đầy đủ với FakeDrive, thiếu dung lượng, throttle→resume) – PASS.
- [x] Đã chạy thử container, UI load OK, kiểm tra link OK, đường lỗi token OK.

## Chưa kiểm chứng (cần người dùng có OAuth client thật)
- [ ] Đăng nhập Google thật + copy thật từ link công khai / tài khoản nguồn.
- [ ] Hành vi thực tế của lỗi 750GB/ngày (reason trả về có thể khác `userRateLimitExceeded`).

## Ý tưởng tiếp theo
- Thông báo (email/Telegram) khi job xong hoặc bị dừng.
- Ước tính thời gian còn lại, tốc độ GB/giờ.
- Khi link gốc lỗi rồi "Thử lại", nếu hoá ra là thư mục thì hiện chưa tự quét lại (item gốc lỗi được coi là file).
- Tuỳ chọn chuyển quyền sở hữu thay vì copy (consumer account hỗ trợ pending owner).
- Chỉ định thư mục/regex loại trừ.
