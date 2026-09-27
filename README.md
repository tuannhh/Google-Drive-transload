# Google Drive Transload

🌐 **English** | [Tiếng Việt](README.vi.md)

> ☕ **If you find this tool useful, you can buy me a coffee** · *Nếu thấy tool hữu ích, bạn có thể tặng tôi một cốc cafe*
>
> **00004657001** – **TPBank (Tienphong Bank)** – **BUI MINH TUAN**

An open-source web tool to **transfer Google Drive data quickly** from one account to another – even terabytes of it. **Deploy for free** on your own computer with **Docker**, or run it 24/7 on a **VPS**.

- **Fast, zero bandwidth**: uses Google's server-side copy (`files.copy`) – nothing is downloaded and re-uploaded, so even multi-TB transfers cost no bandwidth and the cheapest VPS is enough.
- **2 modes**
  - **A. I only have public share links** → sign in to the **destination** account only.
  - **B. I can sign in to the source account** → sign in to **source + destination**; can transfer the **entire My Drive**. The tool temporarily shares items with the destination account and removes those permissions when done.
- Input: paste one link, many links (one per line), or **import an Excel file** (`.xlsx`, `.xls`, `.csv`, `.txt` – hidden hyperlinks in cells are read too).
- Keeps the **folder structure**.
- **Detects and warns** when the destination account **does not have enough storage** (checked after scanning; stops safely if it fills up mid-way).
- **Runs in the background** and **resumes automatically** after a container/VPS restart (state stored in SQLite); pauses and retries automatically when Google's **750GB/day limit** is hit.
- **.txt log** per job: `data/logs/job_<id>.txt` – every file marked `[OK]` / `[LỖI]` (error) / `[BỎ QUA]` (skipped), plus a summary and error list at the end.

> The web UI and logs are currently in Vietnamese.

---

## 1. Create a Google OAuth client (once, ~10 minutes)

1. Go to <https://console.cloud.google.com/> → create a new project (e.g. `drive-transload`).
2. **APIs & Services → Library** → search **Google Drive API** → **Enable**.
3. **APIs & Services → OAuth consent screen** (Google Auth Platform):
   - User type: **External** → fill in app name and email.
   - **Data access / Scopes**: add `https://www.googleapis.com/auth/drive`.
   - **Audience / Test users**: add the source and destination account emails.
   - ⚠️ **Important:** click **Publish app** (switch to *In production*). In *Testing* mode, refresh tokens **expire after 7 days**, so large jobs (several TB, running for days) may stop mid-way. An unverified app still works for yourself; at sign-in you will see *"Google hasn't verified this app"* → click **Advanced → Go to … (unsafe)**.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**
   - Application type: **Web application**
   - **Authorized redirect URIs**: `http://localhost:8080/oauth/callback`
     (with your own domain: `https://your-domain/oauth/callback`)
5. Copy the **Client ID** and **Client secret**.

## 2. Run on your computer (Docker Desktop)

```bash
git clone https://github.com/tuannhh/Google-Drive-transload.git
cd Google-Drive-transload
cp .env.example .env        # then set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
docker compose up -d --build
```

Open <http://localhost:8080> →

1. **Sign in with Google** (destination; in mode B also sign in to the source account).
2. Choose mode A/B, pick the accounts, paste links or import Excel → **Create job & start scanning**.
3. The tool scans the whole folder tree, totals the size and compares it with the free space at the destination:
   - Enough → transfer starts automatically (if *Auto start* is on).
   - Not enough → a red **WARNING** is shown; upgrade/free up storage and click *Re-check storage*, or *Run anyway* (transfer until full).
4. Track progress, view/download the **.txt log**, *Retry failed items*, *Pause/Resume/Cancel*.

> After signing in you can close the browser – the job keeps running in the container.

## 3. Run 24/7 even when your COMPUTER IS OFF (recommended for large data)

Docker on a personal computer stops when the computer is off (the job **resumes automatically** when it starts again). To keep running while your computer is off, run the same Docker Compose setup on a **VPS** (1 vCPU / 1GB RAM is enough, since copying happens on Google's side):

```bash
# On the VPS (Ubuntu)
curl -fsSL https://get.docker.com | sh
git clone https://github.com/tuannhh/Google-Drive-transload.git && cd Google-Drive-transload
cp .env.example .env && nano .env      # set Client ID/Secret and APP_PASSWORD
docker compose up -d --build
```

Sign in to Google through an **SSH tunnel** (no domain/HTTPS needed, because Google only allows `http://` redirects for `localhost`):

```bash
ssh -L 8080:localhost:8080 user@VPS_IP
```

Then open <http://localhost:8080> on your computer, sign in and create jobs. Afterwards you can close SSH and **turn off your computer** – the VPS keeps going. Reopen the tunnel any time to check progress.

With a domain + HTTPS (e.g. via Caddy/Nginx): set `BASE_URL=https://your-domain` and add the matching redirect URI in Google Cloud. **Remember to set `APP_PASSWORD`.**

## 4. Limitations

| Issue | How the tool handles it |
|---|---|
| Google limits copying to ~**750GB/day** per account → 1TB takes about 1.5 days | Detected automatically; pauses `THROTTLE_PAUSE_MINUTES` (default 60) then resumes |
| Owner has **disabled download/copy** | Mode A: logged as `[LỖI]`. Mode B: shares Editor access first, then copies successfully |
| Google Sites, My Maps | Cannot be copied via the API → `[BỎ QUA]` |
| Shortcuts | `[BỎ QUA]` – add the original link if needed |
| Destination fills up mid-way | Stops safely (*Stopped – out of storage*); click *Resume* after upgrading |
| Container/VPS restarts | Resumes automatically; duplicates are avoided via `appProperties` |
| Copies | Owned by the destination account; version history, comments and old sharing settings are not carried over |

## 5. Log file

`data/logs/job_<id>.txt` (download from the UI with the **⬇ Log .txt** button):

```
[2026-09-28 09:00:01] [INFO] === BẮT ĐẦU JOB #1: 3 link – 2026-09-28 0900 ===
[2026-09-28 09:02:10] [INFO] Quét xong: 120 thư mục, 8 500 file, tổng 1.20 TB
[2026-09-28 09:02:11] [INFO] Dung lượng đích: đã dùng 15.00 GB / 2.00 TB, còn trống 1.99 TB; cần 1.20 TB
[2026-09-28 09:02:15] [OK] file /Photos/2019/IMG_0001.JPG (4.20 MB)
[2026-09-28 09:02:16] [LỖI] file /Docs/locked.pdf (2.00 MB): Chủ sở hữu đã chặn tải xuống/sao chép file này (403 cannotCopyFile)
[2026-09-28 13:40:00] [TẠM NGHỈ] Google đang giới hạn (thường do vượt 750GB/ngày ...). Tự động chạy lại lúc 14:40 28/09
...
======================================================================
KẾT QUẢ JOB #1: ...
Chuyển xong        : 8490/8500 file (1.19 TB), 120/120 thư mục
Lỗi                : 7
Bỏ qua             : 3
--- DANH SÁCH LỖI (không chuyển được) ---
[LỖI] /Docs/locked.pdf | https://drive.google.com/open?id=... | Chủ sở hữu đã chặn ...
```

## 6. `.env` settings

| Variable | Meaning |
|---|---|
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | OAuth client (step 1) |
| `BASE_URL` | URL you open the tool at; redirect URI = `BASE_URL/oauth/callback` |
| `APP_USERNAME`, `APP_PASSWORD` | Protect the UI with Basic Auth. Empty = no lock |
| `PORT` | Published port (default 8080) |
| `COPY_WORKERS` | Files copied in parallel (default 4) |
| `THROTTLE_PAUSE_MINUTES` | Pause length when Google rate-limits |
| `MAX_ATTEMPTS` | Retries per file on temporary errors |

## 7. Development

```bash
docker build -t gdrive-transload:dev .
docker run --rm gdrive-transload:dev python -m pytest -q tests
```

Layout: `app/main.py` (FastAPI + OAuth + API), `app/worker.py` (background worker), `app/drive.py` (Drive API), `app/links.py` (link/Excel parsing), `app/db.py` (SQLite), `app/static/index.html` (UI). Development progress & notes for AI agents: [`memory-bank/`](memory-bank/).

## ☕ Support

If this tool saves you time, feel free to buy me a coffee:

**00004657001** – **TPBank (Tienphong Bank)** – **BUI MINH TUAN**

Thank you! ❤️
