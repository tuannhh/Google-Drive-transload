# System patterns & quyết định thiết kế

## Copy phía server
Dùng `POST files/{id}/copy` bằng token tài khoản **đích** → Google tự nhân bản, không tải về.
- Chế độ `public`: đích copy trực tiếp file công khai (gửi header `X-Goog-Drive-Resource-Keys` nếu link có resourcekey).
- Chế độ `account`: liệt kê bằng token **nguồn**; trước khi copy, nguồn chia sẻ các item gốc cho email đích
  (role `writer`, không gửi email) → quyền kế thừa xuống con. Nếu copy lỗi 404/403 → chia sẻ trực tiếp file đó rồi thử lại.
  Hoàn tất job thì gỡ các quyền đã tạo (bảng `shared_perms`).
- Mỗi bản copy/thư mục có `appProperties.transloadSrc=<src_id>` → khi chạy lại sau sự cố, item có `maybe_done=1`
  sẽ được tìm bằng `find_copied()` trước để tránh copy trùng. Thư mục gốc của job dùng marker `transload-job-<id>`.

## Máy trạng thái job (`jobs.status`)
```
scanning -> awaiting_confirm (thiếu dung lượng / auto_start=0) -> running
scanning -> running (đủ dung lượng + auto_start)
running  -> completed | completed_with_errors
running  -> throttled (wait_until; worker tự chuyển về running) | paused_quota | paused | cancelled | error
```
Worker (1 thread) lấy job đầu tiên có status `scanning`/`running` theo id. Người dùng đổi status qua
`POST /api/jobs/{id}/action` (start/resume/pause/recheck/cancel/retry_failed); worker kiểm tra status mỗi lô (`_check_control`).

## Quét (resumable)
BFS: item thư mục có `listed=0` sẽ được liệt kê, chèn con (INSERT OR IGNORE, unique `(job_id,parent_item_id,src_id)`),
rồi `listed=1` trong cùng transaction. Item gốc có `parent_item_id=0`. `roots_ready=1` khi đã chèn xong các gốc.

## Chuyển
1. Tạo thư mục gốc đích (`dest_root_id`).
2. Tạo thư mục theo từng `depth` (song song trong cùng tầng).
3. Copy file theo lô 500, `ThreadPoolExecutor(COPY_WORKERS)`, mỗi thread có client riêng (`threading.local`).

## Xử lý lỗi (`_TransferCtx._handle_error`)
- `storageQuotaExceeded` → dừng job `paused_quota`.
- Rate limit (403 userRateLimitExceeded/429/5xx): `_call` tự backoff ngắn; nếu ≥3 lỗi rate liên tiếp → `throttled`
  và nghỉ `THROTTLE_PAUSE_MINUTES` (đây là cách xử lý giới hạn ~750GB/ngày).
- Lỗi vĩnh viễn (`cannotCopyFile`, `notFound`, ...) → `failed` ngay; lỗi khác thử lại tối đa `MAX_ATTEMPTS`.
- `RefreshError` → job `error` ("đăng nhập lại").

## Dung lượng
Sau quét: `about.storageQuota` của đích; cần = tổng `size` item file pending. `dest_free_bytes=-1` = không giới hạn,
NULL = chưa kiểm tra. Không đủ → `awaiting_confirm` + cảnh báo trong log & UI.
