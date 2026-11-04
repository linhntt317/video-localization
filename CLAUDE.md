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

## Quy ước
- Giao diện tiếng Việt. Không lưu API key vào project.json. Chỉ dùng với video có quyền chỉnh sửa/đăng.
