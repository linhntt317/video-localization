# Video localization (web app) — bàn giao cho Claude Code

Công cụ: từ video có sub cứng (Trung/Anh) → OCR → SRT → dịch Việt (Claude API hoặc nhập SRT) → xoá sub cũ bằng nhoè → thêm logo, watermark góc, watermark bay, sub Việt → xuất MP4 chuẩn YouTube.

## Cấu trúc
- `app.py` Flask (127.0.0.1:8765). Env: `PORT`, `HOST`, `NO_BROWSER`, `ALLOWED_ORIGINS` (CORS, kèm Private-Network header), `ACCESS_TOKEN` (header `X-Access-Token` hoặc `?token=`; bắt buộc nếu HOST != 127.0.0.1).
- `studio/` util (ffmpeg/tesseract), ocr, translate (Claude API, model mặc định claude-sonnet-5-5), render (ffmpeg + OpenCV), srt.
- `static/` giao diện vanilla JS/CSS (app.js, style.css, index.html). `app.js` dùng `CFG.base`/`U()` để gọi máy xử lý khi host ở web khác.
- `Dockerfile`, `docker-compose.yml`, `start.bat`, `setup.bat` (cài Python/ffmpeg/Tesseract + tải tessdata eng, chi_sim vào `tessdata/`, set TESSDATA_PREFIX).

## Đã deploy
- Vercel project `video-localization` (id prj_5i2ppCV5VvxgGgvAhCAVPeDDIuEL, tài khoản cá nhân hanvubang16-3091): https://video-localization.vercel.app — chỉ giao diện tĩnh (index.html, app.js, style.css, mobile.css, logo.png). app.js/style.css/mobile.css trên Vercel là bản minify (esbuild) của `static/`; sửa `static/` xong chạy `./build_site.sh` (tạo `site/`, đã gitignore) rồi `./build_site.sh --deploy`.
- Vercel không chạy được ffmpeg/Tesseract → phải có "máy xử lý" riêng (app local hoặc Docker/VPS; điện thoại dùng cloudflared tunnel + ACCESS_TOKEN).

## Việc còn dở
1. Laptop Windows: ffmpeg + libass OK, Python 3.13 OK, **Tesseract chưa cài** (UAC bị huỷ). `setup.bat` đã thêm thử `scoop install tesseract`; nếu không được thì cài UB-Mannheim.TesseractOCR (cần quyền admin) hoặc đặt `TESSERACT_BIN`.
2. Chưa test: gọi Claude API thật, OCR/xuất video qua UI trên Windows, kết nối từ trang Vercel (Chrome/Edge) tới 127.0.0.1, bố cục mobile (iPhone Safari không gọi được http://127.0.0.1 → cần https tunnel/server).
3. `mobile.css` đã được nạp trong index.html (trước đây quên link) và đã kiểm tra bằng giả lập iPhone 13/Pixel 7 (Playwright); vẫn nên xem trên điện thoại thật.
4. Logo mặc định đổi tên `assets/logo.png` (dự án cũ lưu `dauxanh_logo.png` vẫn được map sang). Chữ watermark mặc định trong `studio/render.py` vẫn là "ĐẬU XANH STUDIO" (nội dung kênh, chưa đổi).

## Đã kiểm tra (Linux, Chromium headless)
- Luồng đầy đủ khi giao diện ở origin khác máy xử lý (giống Vercel → 127.0.0.1) + ACCESS_TOKEN: tải video, dò vùng sub, OCR (eng), nhập SRT, xem thử, xuất MP4 đạt mọi mục kiểm tra YouTube.
- Hộp "Máy xử lý": nút Thử kết nối, báo lỗi cụ thể (sai token, CORS/ALLOWED_ORIGINS, http từ trang https, Safari/iOS).

## Máy xử lý trên điện thoại (Colab) — cách chính cho người dùng chỉ có điện thoại
- `colab.ipynb`: cài ffmpeg/Tesseract, gắn Drive (`video-localization/{videos,exports,backup}`), clone repo, chạy app + cloudflared, in link `<tunnel>/?token=<mã>`. Không cần Vercel: Flask tự phục vụ giao diện và chèn `window.VL_EMBEDDED=1` để `app.js` gọi API cùng origin.
- Env mới trong `app.py`: `MEDIA_DIR` (+ `MEDIA_SRC` tự chép từ Drive sang ổ Colab, `/api/media`, `/api/media/sync`), `EXPORT_DIR` (chép bản xuất + SRT), `BACKUP_DIR` (sao lưu/khôi phục project.json). Tạo dự án từ `{"media": "ten.mp4"}`.
- `app.js`: `openProject` nối lại thanh tiến độ nếu có tác vụ đang chạy; `?server=&token=` vẫn dùng được cho trang Vercel.
- Cloudflare quick tunnel giới hạn upload ~100 MB và request ~100 s → video lớn phải đi qua Drive, không upload trực tiếp.
- Vercel: lần thử `create_deployment` gitSource báo `git_info_fail` (Vercel hanvubang16-3091 chưa kết nối repo linhntt317/video-localization) và đã ghi Root Directory=`static` vào project. Bản Vercel hiện cũ, không còn cần cho luồng Colab.
- Chưa chạy được trên Colab thật (chỉ giả lập thư mục Drive trên Linux).

- Hugging Face Spaces Docker cần gói PRO (xác nhận qua ảnh chụp màn hình của người dùng, 4/11/2026) → không dùng cho phương án miễn phí. Đã bỏ workflow sync-hf. Dockerfile vẫn chạy user uid 1000.
- Tải video từ link: `/api/import_url` (link trực tiếp hoặc Google Drive qua gdown).

- Bước 3 có khung "Tìm và bổ sung câu bị sót": thanh thời gian + đánh dấu câu, gợi ý khoảng trống ≥3s, thêm câu tại điểm đang xem, quét lại một đoạn (`POST /api/p/<pid>/rescan`, `merge_cues` chỉ thêm câu không trùng >40%, không xoá câu cũ; ngưỡng mặc định hạ xuống conf 40 / seg 55).

## Quy ước
- Giao diện tiếng Việt. Không lưu API key vào project.json. Chỉ dùng với video có quyền chỉnh sửa/đăng.
