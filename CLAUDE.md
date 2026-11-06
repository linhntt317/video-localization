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

## OCR (studio/ocr.py) — đã đo bằng video tổng hợp 43 câu (chữ Trung+Anh, 2 dòng, câu 0,7-2,2s, nén H.264)
- Lỗi cũ: chỉ coi pixel trắng ≥210 là chữ (chữ xám/mờ dần/nén/nền sáng hỏng: 0-21/43), so khung đổi câu bằng sai khác trung bình (câu mới giống câu cũ → dùng lại chữ cũ), ngưỡng tin cậy 55/72 làm rớt chữ khó, detect_bands cũng dùng ngưỡng 215 cố định.
- Mới: `_text_mask` (top-hat + Otsu + ngưỡng gần mức sáng cao nhất của chữ), so khung bằng tỉ lệ hình dạng, đọc 3 phiên bản ảnh (thích nghi nhị phân/mịn + trắng cũ) rồi lấy conf cao nhất, nới dải 12%, mặc định min_conf 40 / seg_min 55. Kết quả (đúng/43): sạch 43, chữ nhỏ 40, xám 43, mờ dần 43, nền sáng 42, nén rất mạnh 24.
- `thr` giờ là độ nhạy (thấp = nhạy hơn). Chưa kiểm tra trên video thật của người dùng.

- Công cụ chẩn đoán: `POST /api/p/<pid>/probe` (`ocr.probe_frame`) + nút "Đọc thử khung này" ở bước 3: cho từng dải xem ảnh cắt, ảnh chữ tách được, chữ đọc ra, độ tin cậy và lý do bị bỏ (no_text/unreadable/low_conf/ok); có nút tạo câu từ chữ đọc được.
- Tốc độ: Tesseract ~80% thời gian; `ocr.ref=false` (bỏ dòng đối chiếu) nhanh ~2,5 lần; `_ocr_best` dừng sớm khi conf ≥86.

- Whisper (`studio/asr.py`, faster-whisper, `requirements-asr.txt` tuỳ chọn; Colab tự cài): `POST /api/p/<pid>/asr` lưu `project["asr"]={model,lang,segments}`; giao diện đối chiếu với câu OCR (dung sai 0,4s), đánh dấu đoạn nghi sót, thêm câu (`asr:true`), quét lại OCR theo `ranges` (`/rescan`). `asr.LOADER` thay được khi kiểm thử. **Chưa chạy được Whisper thật** (sandbox chặn tải mô hình từ Hugging Face): chỉ kiểm thử bằng mô hình giả; GPU/cuDNN trên Colab cũng chưa kiểm tra.

- `POST /api/p/<pid>/ocr` nhận `ocr.asr={on,model,lang,add}`: OCR (0-50%) → Whisper (50-80%) → `ocr_ranges` quét lại OCR các đoạn `asr_uncovered` với ngưỡng thấp (80-97%) → tuỳ chọn thêm câu Whisper (`asr:true`). Lỗi Whisper chỉ ghi `result.asr_error`, không mất kết quả OCR. Đã kiểm thử bằng mô hình giả (`asr.LOADER`).

- `video_error(p)` (app.py): nếu file video của dự án mất (Colab khởi động lại, dự án khôi phục từ BACKUP_DIR mà video chưa chép về) thì frame/ocr/rescan/asr/probe/preview/render trả 503 kèm lý do; nếu video còn ở `MEDIA_SRC` thì tự `start_sync()`. `GET /api/p/<pid>` kèm `_video_error`. `loadImg` hiện lớp thông báo + tự thử lại mỗi 5s khi đang chép.

- Tốc độ OCR (đo 4/11): mỗi lần gọi Tesseract riêng ~0,12s (chủ yếu khởi động); `_ocr_batch` gộp ~16 ảnh vào một lần chạy (danh sách ảnh, đọc `page_num` trong TSV) + `--oem 1` → nhanh ~5-8 lần. `run_ocr` chạy song song: mặt nạ theo cửa sổ 24 khung (ThreadPool), Tesseract theo lô trong ThreadPool trong lúc vẫn đọc tiếp khung hình. Video thử 64s: sạch 13s→3s, mờ dần 28s→5s, độ chính xác không đổi. Tesseract đã dùng hết CPU nên thêm luồng không nhanh hơn nữa.
- Xác thực Whisper bằng OCR kỹ: `ocr.deep_scan` (4 mức nhạy × fps 12-15, thử từng dòng làm dòng chính, `plausible()` lọc chuỗi rác, dừng sớm khi conf≥70) qua `deep_ranges` (chỉ giữ câu thuộc lõi đoạn). Pipeline `/ocr` với `ocr.asr.on` và `/rescan {deep:true}` chỉ thêm câu khi OCR thấy chữ thật; KHÔNG còn tự thêm chữ Whisper (nút "+ Thêm" thủ công vẫn có). `merge_cues` gỡ trùng cả giữa các câu mới. Đoạn OCR vẫn không thấy chữ giữ ở hàng đỏ.

- Công cụ riêng (`studio/tools.py` + mục "Công cụ" ở `static/`): ghép (`merge`: copy nếu cùng `_sig` codec/kích thước/pix_fmt/fps/âm thanh, ngược lại concat filter + anullsrc cho video không tiếng), cắt (`cut`: `-c copy` nhanh hoặc libx264 chính xác, nhiều đoạn, tuỳ chọn nối), tách MP3 (libmp3lame). API `/api/tools/*`: `sources`, `source` (đăng ký → `sid`), `frame/<sid>`, `upload`, `merge|cut|audio` (tác vụ nền key `_tools`), `task`, `outputs`, `file/<name>`. Tải link dùng chung `/api/import_url` với `to:"tools"`. File ở `tools/in` và `tools/out` (gitignore); kết quả chép thêm vào `EXPORT_DIR`. `runTask(root,onDone,btn,base)` poll theo `root._poll`.

- Chữ mỏng (đo 5/11, video thử 43 câu, chữ trắng KHÔNG đậm): lỗi cũ là `medianBlur(3)` trong `_text_mask` xoá nét 1px và chỉ đọc ảnh nhị phân. Sửa: bỏ median, thêm ứng viên `_prep_gray` (ảnh xám phóng to ~120px, kéo giãn tương phản) thử cuối chuỗi `run_jobs` (4 vòng). Đúng/43: viền mỏng 14→34, không viền 22→34, mỏng trên nền sáng 25→42, chữ nhỏ 40→43; chữ đậm vẫn 43. Mức cực khó (trắng mỏng trên nền xám sáng, viền 0,6px) vẫn ~80%.

- Xử lý trên trình duyệt (tab "Trên máy bạn", `static/vendor/ffmpeg/` = @ffmpeg/ffmpeg 0.12.10 esm + @ffmpeg/core 0.12.6 đơn luồng, wasm 32MB, phục vụ cùng origin, `Cache-Control` 30 ngày, `.wasm` mimetype trong app.py). `lcWith` gắn `File` bằng WORKERFS (không nạp cả file), `lcExec`/`lcRead`; chỉ cắt `-c copy` (+nối concat) và `libmp3lame`. Đã kiểm thử trên Chromium headless (file 250MB cắt 10s trong ~1s), **chưa thử trên Safari/iPad thật**; không dùng được khi giao diện chạy từ Vercel (không có /vendor).

- Lồng tiếng (`studio/tts.py`, edge-tts; `tts.ENGINE` thay được): mỗi cue có `dub={key,dur,voice,text,user}`, file `projects/<pid>/tts/<key>.mp3`; `tts.fit(cue, slot)` quyết định tốc độ/trạng thái (ok|fast|long|none) và PHẢI khớp `dubFit()` trong app.js; slot = độ dài câu + tối đa 0,25s khoảng trống. `build_track` ghép mọi giọng (atempo) vào `tts/track.wav`; `render_final` trộn bằng sidechaincompress (duck) / volume / amix theo `settings.dub`. API: `/api/tts/voices`, `/api/p/<pid>/tts/synth|cue|audio/<key>`, `/api/p/<pid>/shorten` (Claude rút gọn + tạo lại giọng). `project["dub"]={voice}`. Đã kiểm thử bằng giọng giả (tần số theo câu): giọng đúng chỗ, tiếng gốc giảm ~7 lần khi có giọng; **chưa chạy Edge TTS thật** (sandbox chặn mạng ra ngoài) và chưa nghe thử bằng tai.
- Hướng phát triển giống 4kstudio.vn (chỉ xem ảnh trang chủ, 5/11): đã có OCR/dịch/làm mờ/logo/phụ đề/lồng tiếng; chưa có timeline phụ đề, dán link Douyin/TikTok/YouTube (yt-dlp), tài khoản/credits (người dùng chọn dùng cá nhân trước).

- Máy nhà + Tailscale (người dùng chọn, 5/11): `local.env` (đọc bởi `load_local_env()` đầu app.py, không đè biến môi trường có sẵn; mẫu `local.env.example`), `start_tailscale.bat` (lấy IP bằng `tailscale ip -4`, HOST=IP Tailscale, token trong `access_token.txt`, link vào `link.txt`, vòng lặp tự chạy lại), `install_autostart.bat` (schtasks onlogon + powercfg không ngủ khi cắm sạc). **Chưa thử trên Windows thật** (mới kiểm thử phần đọc local.env trên Linux). Qua Tailscale không có giới hạn upload 100MB của Cloudflare (chưa xác nhận bằng thực nghiệm).

## Quy ước
- Giao diện tiếng Việt. Không lưu API key vào project.json. Chỉ dùng với video có quyền chỉnh sửa/đăng.

## OCR trên video thật (6/11/2026) — 第001-005集.mp4, 9 phút 8 giây, 1882x1080 ~30fps, sub Trung 1 dòng, dải 0.825–0.905
- Mốc so sánh: bản desktop (repo pj, RapidOCR, lấy mẫu 0,2s + tinh chỉnh mốc theo fps gốc) ra 250 câu, 0 rác. Bản này cũng sót câu thật 1 chữ (啊/不/不疼, đã xem ảnh khung hình).
- Lỗi Windows: gọi Tesseract bằng tên cấu hình `tsv` cần file `tessdata/configs/tsv`; `setup.bat` trỏ TESSDATA_PREFIX vào thư mục chỉ có *.traineddata → Tesseract in chữ thường, TSV rỗng → **0 câu**. Sửa: `-c tessedit_create_tsv=1 -c tessedit_create_txt=0` (`_TSV`). `util.probe` (và các lệnh `text=True` khác) giải mã bằng codepage hệ thống → crash với tên file tiếng Trung; sửa bằng `encoding="utf-8"`.
- Code cũ + Tesseract + dải đúng: 245 câu, 4 rác, sót 10 so với bản desktop. Bản 211 câu (9 rác, sót 57) nhiều khả năng do dải/dữ liệu Tesseract khác, KHÔNG chủ yếu do fps=6.
- Sau sửa (Tesseract): 257 câu, sót 10, tìm thêm câu 1 chữ thật; rác `一`/`一一` do đổi `-`→`一` đã chặn trong `_cue_ok` (chưa chạy lại toàn video sau khi chặn).
- RapidOCR (`engine="rapid"`, thử nghiệm): đọc 1 lần/câu ra 201 câu, sót 50 — bộ dò chữ của RapidOCR rất nhạy với cách cắt (thêm 12px lề là rỗng). Đã đổi sang đọc lần lượt 3 phiên bản ảnh (`_rapid_best`) nhưng CHƯA đo lại toàn video → mặc định vẫn là Tesseract. `detect_bands` chỉ dùng RapidOCR khi engine khác tesseract (dò cũ ra dải 0.72–1.00 trên video này → 0 câu; đã thêm cắt đệ quy dải > 8,5%).
