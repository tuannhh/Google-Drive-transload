# Project brief – Google Drive Transload

## Mục tiêu
Tool mã nguồn mở giúp chuyển dữ liệu Google Drive **nhanh chóng** từ tài khoản này sang tài khoản khác (kể cả hàng TB),
**triển khai miễn phí** qua Docker trên máy cá nhân hoặc chạy 24/7 trên VPS.

## Yêu cầu (2026-09-27)

- Nhập nguồn bằng: paste 1 link, nhiều link, hoặc import file Excel.
- Hai lựa chọn:
  1. Chỉ có link Google Drive chia sẻ công khai → chỉ yêu cầu đăng nhập tài khoản **đích**.
  2. Có tài khoản nguồn → đăng nhập tài khoản **nguồn** và tài khoản **đích**.
- Tự phát hiện & cảnh báo nếu tài khoản đích **không đủ dung lượng**.
- Chạy ngầm, kể cả khi tắt máy tính (→ chạy Docker trên VPS; trên máy cá nhân thì tự chạy tiếp khi bật lại).
- Ghi log `.txt`: chuyển xong / lỗi không chuyển được / ...
- Ghi memory bank + push GitHub (repo "Google Drive transload") để AI agent ở máy khác làm tiếp.
- Trước mắt triển khai trên **Docker**.

## Người dùng
- GitHub: `tuannhh`. Nói tiếng Việt → UI, log, tài liệu bằng tiếng Việt.
