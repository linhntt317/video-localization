# Video localization

Web app chạy trên máy của bạn: từ một video có sub cứng (Trung, Anh...) làm ra video sub tiếng Việt, có logo, watermark, và xuất đúng chuẩn đăng YouTube.

Luồng làm việc gồm 4 bước:

1. **Video**: chọn file hoặc dán đường dẫn.
2. **Quét sub**: tự dò vùng sub cứng, chọn ngôn ngữ từng dòng, quét OCR ra danh sách câu có thời gian.
3. **Phụ đề**: sửa lỗi OCR, dịch bằng Claude (hoặc nhập SRT dịch sẵn), xuất SRT.
4. **Xuất video**: xoá sub cũ (tô lại nền rồi làm nhoè), thêm logo, chữ watermark góc, chữ watermark bay vòng, ghi sub Việt đúng chỗ sub cũ, xuất MP4 chuẩn YouTube và kiểm tra lại file.

## 1. Cài đặt (làm một lần)

Cần 3 thứ ngoài Python: **ffmpeg** (bản có libass), **Tesseract OCR** và **Python 3.10+**.

Tuỳ chọn (thử nghiệm): với sub **tiếng Trung / tiếng Anh** có thể dùng **RapidOCR** thay Tesseract
(`pip install rapidocr-onnxruntime`, rồi chọn "Bộ đọc chữ: RapidOCR" trong tuỳ chọn nâng cao của bước quét).
Mặc định vẫn là Tesseract vì RapidOCR chưa được kiểm chứng trên toàn bộ một video thật.

### Windows
```
winget install Python.Python.3.12
winget install Gyan.FFmpeg
winget install UB-Mannheim.TesseractOCR
```
Khi cài Tesseract (bản UB Mannheim), ở bước chọn thành phần hãy tick thêm gói ngôn ngữ cần dùng, ví dụ **Chinese (Simplified)**, **Chinese (Traditional)**, **Korean**, **Japanese**. Tiếng Anh có sẵn.
Sau khi cài xong, mở lại cửa sổ dòng lệnh hoặc khởi động lại máy để Windows nhận PATH mới.

### macOS
```
brew install python ffmpeg tesseract tesseract-lang
```

### Ubuntu / Debian
```
sudo apt install python3 python3-venv ffmpeg tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim tesseract-ocr-chi-tra tesseract-ocr-kor tesseract-ocr-jpn
```

## 2. Chạy

- **Windows**: bấm đúp `start.bat`.
- **macOS / Linux**: mở terminal tại thư mục này và chạy `./start.sh` (macOS có thể bấm đúp `start.command`).

Lần đầu chạy sẽ tự tạo môi trường và cài thư viện Python. Trình duyệt tự mở trang `http://127.0.0.1:8765`. Đóng cửa sổ dòng lệnh để tắt ứng dụng.

Ba chấm ở góc phải trang (ffmpeg, libass, Tesseract) màu xanh nghĩa là đã sẵn sàng. Nếu đỏ, xem phần xử lý lỗi bên dưới.

## 3. Cách dùng

**Bước 1.** Kéo thả video vào ô, hoặc dán đường dẫn file đã có trên máy (không phải chép lại, nhanh hơn với file lớn).

**Bước 2.**
- Kéo thanh thời gian tới một khung có sub, bấm **Tự dò vùng sub** (hoặc kéo thủ công các dải màu cho khớp từng dòng chữ).
- Chọn ngôn ngữ cho từng dòng. Ví dụ dòng trên là Trung, dòng dưới là Anh.
- Chọn **Dùng dòng này để dịch**: dòng chính quyết định thời gian từng câu, các dòng còn lại chỉ để đối chiếu.
- Bấm **Quét OCR**.

**Bước 3.**
- Câu có "tin cậy" thấp (đỏ) nên kiểm tra lại. Bấm vào câu để xem khung hình tương ứng.
- **Dịch bằng Claude**: dán API key, chỉnh quy tắc và bảng tên nhân vật rồi dịch. Quy tắc mặc định có sẵn, bạn sửa theo dự án. Key chỉ gửi từ máy bạn tới Anthropic, không lưu vào file dự án; chỉ lưu trong trình duyệt nếu bạn tick "Nhớ key".
- **Nhập SRT Việt**: nếu dịch ở nơi khác, dán hoặc chọn file SRT. Số câu bằng nhau thì khớp theo thứ tự, khác nhau thì khớp theo thời gian.
- **Xuất SRT**: tải SRT gốc hoặc SRT Việt bất cứ lúc nào.

**Bước 4.**
- Xem thử khung hình đúng như bản xuất, chọn câu khác ở ô phía trên để xem ở các cảnh khác nhau.
- Chỉnh vùng sub cũ, logo, hai loại watermark, font và màu sub Việt, chất lượng xuất. Mọi thay đổi tự xem thử lại.
- Bấm **Xuất video chuẩn YouTube**. Xong sẽ có video để xem ngay, nút tải MP4/SRT và bảng kiểm tra chuẩn YouTube.

## 4. Chuẩn YouTube của file xuất

H.264 (High profile), yuv420p, âm thanh AAC-LC 48 kHz stereo, `faststart` (thông tin moov ở đầu file), khoảng cách keyframe 2 giây, giữ nguyên độ phân giải và fps của video gốc (hoặc ép về 1080p/720p nếu chọn). Chất lượng "Cân bằng" dùng CRF 19; "Đẹp nhất" dùng CRF 17 nhưng xuất lâu hơn.

## 5. Thêm font, thêm logo

- Bỏ file `.ttf` hoặc `.otf` vào thư mục `fonts/`, mở lại app: font xuất hiện trong mục **Phụ đề tiếng Việt**. Chọn font có hỗ trợ tiếng Việt có dấu.
- Logo mặc định là `assets/logo.png`. Ở bước 4, mục **Logo**, bạn có thể tải logo khác lên (PNG nền trong suốt cho đẹp nhất).

## 6. Xử lý lỗi thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| Chấm **ffmpeg** đỏ | Cài ffmpeg rồi mở lại app. Có thể trỏ thẳng bằng biến môi trường `FFMPEG_BIN` (đường dẫn tới ffmpeg.exe). |
| Chấm **libass** đỏ | ffmpeg đang dùng thiếu libass nên không chèn được sub. Dùng bản đầy đủ (Windows: `winget install Gyan.FFmpeg`). |
| Chấm **Tesseract** đỏ | Cài Tesseract, hoặc đặt biến môi trường `TESSERACT_BIN`. |
| "Tesseract chưa có ngôn ngữ ..." | Cài thêm gói ngôn ngữ (Windows: chạy lại bộ cài và tick thêm ngôn ngữ). |
| Không dò thấy vùng sub | Kéo thanh thời gian tới lúc có sub rồi chỉnh các dải bằng tay. Nếu chữ sub không phải màu trắng, đổi "Ngưỡng chữ trắng" trong Tuỳ chọn nâng cao. |
| OCR sót hoặc dư câu | Chỉnh "Độ tin cậy tối thiểu" (nâng lên để bớt câu rác, hạ xuống để bớt sót), tăng số khung/giây, hoặc sửa tay ở bước 3. |
| Còn bóng chữ cũ sau khi xoá | Ở bước 4, kéo "Sub cũ bắt đầu từ" lên cao hơn một chút, hoặc tăng "Độ nhoè". |
| Dải nhoè hơi tối ở cảnh tối | Bình thường: nền được tô lại từ chính màu cảnh xung quanh. |

## 7. Cấu trúc thư mục

```
app.py               máy chủ web (Flask)
studio/              util, ocr, translate, render, srt
static/              giao diện (HTML, CSS, JS)
fonts/               font dùng khi xuất (DejaVu kèm sẵn)
assets/              logo mặc định
projects/            dữ liệu từng dự án (tự tạo): project.json, final.mp4, srt...
```

Dự án nào cũng tự lưu. Muốn xoá thì bấm dấu × ở danh sách dự án (sẽ xoá cả file đã xuất).

## 8. Ghi chú

- Thời gian xoá sub cũ phụ thuộc vào số đoạn có sub: chỉ những đoạn đó mới được xử lý nặng.
- Ứng dụng chỉ lắng nghe trên `127.0.0.1` (máy của bạn), không mở ra mạng ngoài.
- Chỉ dùng với video bạn có quyền chỉnh sửa và đăng tải.

## 9. Dùng giao diện trên web (https://video-localization.vercel.app)

Trang web chỉ là giao diện. Quét OCR và xuất video vẫn chạy trên máy xử lý của bạn.

**Cách 1: app trên máy bạn.** Chạy app như mục 2. `start.bat`, `start.sh`, `start.command` đã tự cho phép trang https://video-localization.vercel.app gọi vào máy (biến `ALLOWED_ORIGINS`; muốn thêm trang khác thì đặt biến này trước khi chạy, cách nhau bằng dấu phẩy).

Mở trang web, bấm "Máy xử lý", để địa chỉ `http://127.0.0.1:8765`, bấm **Thử kết nối** rồi **Lưu và kết nối**. Nếu lỗi, hộp thoại sẽ nói rõ lý do (app chưa chạy, sai mã truy cập, chưa cho phép origin, trình duyệt chặn http...). Chrome/Edge dùng được (nếu Chrome hỏi quyền truy cập thiết bị trong mạng cục bộ, bấm Cho phép); Safari chặn trang https gọi http nên hãy dùng `http://127.0.0.1:8765` trực tiếp.

**Cách 2: server riêng (Docker).**
```
ACCESS_TOKEN=matkhaucuaban ALLOWED_ORIGINS=https://video-localization.vercel.app docker compose up -d --build
```
Đặt server sau HTTPS (Caddy, Cloudflare Tunnel...), rồi nhập địa chỉ và mã truy cập ở hộp "Máy xử lý". Khi mở ra ngoài (`HOST` khác 127.0.0.1) bắt buộc có `ACCESS_TOKEN`.

### Dùng trên điện thoại (máy tính vẫn là máy xử lý)

Điện thoại không gọi được `127.0.0.1` và Safari chặn http, nên cần một địa chỉ https trỏ về máy tính đang chạy app. Cách nhanh, miễn phí: Cloudflare Quick Tunnel.

1. Trên máy tính, đặt mã truy cập rồi chạy app (bắt buộc, vì địa chỉ tunnel là công khai):
   - Windows: `set ACCESS_TOKEN=matkhaucuaban` và `set ALLOWED_ORIGINS=https://video-localization.vercel.app`, rồi chạy `start.bat`.
   - macOS/Linux: `ACCESS_TOKEN=matkhaucuaban ALLOWED_ORIGINS=https://video-localization.vercel.app ./start.sh`
2. Cài `cloudflared` (Windows: `winget install Cloudflare.cloudflared`; macOS: `brew install cloudflared`) rồi chạy: `cloudflared tunnel --url http://127.0.0.1:8765`
3. Nó in ra địa chỉ dạng `https://xxxx.trycloudflare.com`. Trên điện thoại mở https://video-localization.vercel.app, bấm "Máy xử lý", dán địa chỉ đó và mã truy cập.

Lưu ý: tải video lên qua tunnel miễn phí bị giới hạn khoảng 100 MB mỗi lần. Với video lớn, hãy để file trên máy tính và dùng ô "đường dẫn file có sẵn" ở bước 1 (gõ trên điện thoại đường dẫn của máy tính). Tắt tunnel khi không dùng.

### Cập nhật bản trên Vercel

Sau khi sửa `static/`, chạy `./build_site.sh` để tạo thư mục `site/` (đã minify bằng esbuild, kèm logo), rồi `./build_site.sh --deploy` (cần `vercel login`, chọn project `video-localization`).

### Chỉ có điện thoại: dùng Google Colab làm máy xử lý (khuyên dùng, kể cả video 2-4 GB)

Không cần máy tính, VPS hay Vercel. Mở [colab.ipynb trong Colab](https://colab.research.google.com/github/linhntt317/video-localization/blob/claude/web-app-continue-6807bw/colab.ipynb) và bấm ▶ ở **một ô duy nhất** (dạng form, tự cài đặt, gắn Drive, chạy server). Ô đó in ra một link dạng `https://xxxx.trycloudflare.com/?token=...`: mở link đó trên điện thoại là dùng ngay (giao diện do chính máy xử lý phục vụ, không bị chặn http/https hay CORS).

Quy trình với video dài:
1. Trong app Google Drive trên điện thoại, tải video vào **video-localization/videos** (ô 2 tự tạo thư mục).
2. Mở link, ở bước 1 bấm **Làm mới danh sách**: app tự chép video từ Drive sang ổ đĩa nhanh của Colab (có hiện tiến độ). Chọn video trong danh sách để tạo dự án.
3. Quét OCR, dịch, xuất. Nếu điện thoại khoá màn hình giữa chừng, mở lại link và bấm vào dự án: thanh tiến độ tự nối lại, tác vụ vẫn chạy nền trên Colab.
4. Video và SRT xuất xong được chép tự động vào **Drive ▸ video-localization/exports**; tải từ app Drive (ổn định hơn tải 2-4 GB qua trang web).
5. Dự án được sao lưu vào **Drive ▸ video-localization/backup** mỗi lần lưu và tự khôi phục khi chạy lại ô 3.

Giới hạn: giữ tab Colab mở và ô 3 đang chạy; Colab miễn phí tự ngắt sau vài giờ (chạy lại ô 3 lấy link mới, video phải chép lại từ Drive). Colab miễn phí chỉ có CPU nên xuất video dài mất nhiều thời gian; chọn chất lượng "Nhanh". Link chứa mã truy cập, đừng chia sẻ.

Biến môi trường dùng cho cách này (tự đặt trong notebook): `MEDIA_DIR` (thư mục video để chọn), `MEDIA_SRC` (nguồn tự chép sang `MEDIA_DIR`), `EXPORT_DIR` (nơi chép bản xuất), `BACKUP_DIR` (sao lưu project.json).

### Ghi chú về Hugging Face Spaces

Tháng 11/2026, Hugging Face yêu cầu gói trả phí (PRO) cho Space loại Docker và Gradio; loại Static miễn phí nhưng không chạy được ffmpeg/Tesseract, nên **không dùng được cho app này**. Nếu có gói PRO thì Dockerfile trong repo (chạy bằng user uid 1000, cổng 8765, cần secret `ACCESS_TOKEN`) vẫn dùng được, nhưng chưa được kiểm tra.

### Đối chiếu bằng giọng nói (Whisper)

Ở bước 3, khung "Tìm và bổ sung câu bị sót" có mục **Đối chiếu bằng giọng nói (Whisper)**: app nghe toàn bộ video, rồi đánh dấu những đoạn có người nói mà không có câu OCR nào tương ứng (hàng cam/đỏ dưới thanh thời gian). Với các đoạn đó bạn có thể: **Xem** khung hình, **+ Thêm** câu bằng chữ Whisper (có nhãn "từ Whisper"), **Quét kỹ lại OCR các đoạn nghi sót** (hạ ngưỡng, chỉ thêm câu mới), hoặc **Thêm tất cả**.

- Cài trên máy tính: `pip install -r requirements-asr.txt` (Colab đã tự cài). Lần đầu chạy phải tải mô hình về.
- GPU (Colab: Runtime ▸ Change runtime type ▸ T4) nhanh hơn nhiều; trên CPU hãy dùng tiny/base/small.
- Chữ Whisper là lời nói được nhận dạng nên có thể khác phụ đề gốc hoặc sai chính tả; đoạn không có phụ đề mà có tiếng (lời dẫn, nhạc có lời) cũng bị đánh dấu, bạn bỏ qua là được.

**Đối chiếu ngay khi quét OCR:** ở bước 2, tick **"Đối chiếu bằng giọng nói (Whisper) ngay sau khi quét OCR"**. Một lần bấm "Quét OCR" sẽ chạy: OCR → Whisper nghe video → tự quét lại OCR (ngưỡng thấp hơn) đúng những đoạn có tiếng nói mà chưa có câu → (tuỳ chọn) tự thêm các đoạn vẫn thiếu bằng chữ Whisper, có nhãn "từ Whisper". Nếu Whisper lỗi (chưa cài, không tải được mô hình) thì kết quả OCR vẫn được giữ và có thông báo.

**Xác thực bằng OCR kỹ:** app không tự thêm chữ Whisper. Với mỗi đoạn Whisper nghe thấy tiếng mà chưa có câu, OCR quét lại thật kỹ đúng chỗ đó (nhiều mức nhạy, tốc độ khung cao, thử cả hai dòng) và chỉ thêm câu khi OCR thấy chữ thật (có nhãn "quét kỹ"). Đoạn nào OCR vẫn không thấy chữ (lời dẫn, nhạc có lời, hoặc phụ đề quá mờ) được giữ ở hàng đỏ để bạn xem và quyết định (Xem / Quét kỹ / + Thêm bằng chữ Whisper).

## 10. Công cụ riêng: ghép video, cắt video, tách MP3

Bấm nút **Công cụ** ở góc trên (không dính vào quy trình 4 bước). Mỗi công cụ cho chọn video từ: thư mục Drive/máy xử lý, video của các dự án, file tải lên, link (trực tiếp hoặc Google Drive) hoặc đường dẫn trên máy xử lý. Kết quả nằm ở phần **Kết quả** (Tải về / Nghe, Xem / Xoá) và được chép thêm vào thư mục `exports` trên Drive nếu có đặt `EXPORT_DIR`.

- **Ghép video:** chọn nhiều video, sắp thứ tự bằng ↑ ↓. Các video cùng định dạng (codec, độ phân giải, fps, âm thanh) được ghép **không mã hoá lại** (rất nhanh, giữ nguyên chất lượng). Khác định dạng thì tự mã hoá lại về cùng khung hình, video không có tiếng được chèn đoạn im lặng; có thể chọn 1080p/720p/480p.
- **Cắt video:** kéo thanh xem trước để chọn điểm đầu/cuối cho từng đoạn, thêm nhiều đoạn, chọn mỗi đoạn một file hoặc nối thành một. **Cắt nhanh** (mặc định) không mã hoá lại nhưng điểm đầu có thể lệch về keyframe trước đó vài giây; tick **cắt chính xác** nếu cần đúng từng khung (chậm hơn).
- **Tách MP3:** chọn chất lượng 96-320 kbps và (tuỳ chọn) khoảng thời gian. Video không có âm thanh sẽ báo lỗi.

### Xử lý ngay trên máy bạn (không tải video lên)

Tab **Trên máy bạn** trong mục Công cụ chạy ffmpeg ngay trong trình duyệt (ffmpeg.wasm, các file nằm ở `static/vendor/ffmpeg/`): video không rời khỏi thiết bị và không cần máy xử lý. Có **Kiểm tra máy** (thử nạp ffmpeg, thử mã hoá MP3, và thử đọc một video thật của bạn), **Cắt nhanh** (không mã hoá lại, nhiều đoạn, tuỳ chọn nối) và **Tách MP3**.

- Trình duyệt đọc video theo từng phần (không nạp cả file vào RAM), nên cắt/tách từ video lớn được; kết quả nằm trong bộ nhớ nên chỉ hợp khi kết quả nhỏ (vài trăm MB trở xuống).
- Kết quả chỉ nằm trong trang: bấm **Tải về** (hoặc **Lưu/Chia sẻ** nếu thiết bị hỗ trợ) ngay, tải lại trang là mất.
- Lần đầu trình duyệt tải bộ ffmpeg khoảng 32 MB rồi giữ lại. iPhone/iPad giới hạn bộ nhớ mỗi tab chặt, tab có thể bị tắt khi xử lý file lớn: hãy chạy "Kiểm tra máy" trước, việc nặng dùng máy xử lý.
- ffmpeg.wasm (@ffmpeg/ffmpeg 0.12.10, @ffmpeg/core 0.12.6) là mã nguồn mở của dự án ffmpegwasm; xem giấy phép của ffmpeg/x264 nếu bạn phân phối lại.

## 11. Lồng tiếng (giọng đọc AI cho từng câu)

Ở bước 3, bấm **🎙 Lồng tiếng**:
1. Chọn giọng đọc, bấm **Tạo giọng cho câu chưa có**. Mỗi câu có một file giọng riêng (lưu trong thư mục dự án, câu không đổi chữ thì dùng lại).
2. Mỗi câu hiện **độ khớp thời gian**: *Khớp tốt* (giọng vừa khung thời gian), *Tăng tốc xN* (giọng dài hơn nên tự tăng tốc), *Quá dài* (tăng tốc tối đa vẫn không kịp), cùng số ký tự, độ dài giọng và khung thời gian. Có nút **▶ Nghe** (phát theo đúng tốc độ), **− / +** chỉnh tốc độ tay, **Tự động**, **Tạo lại giọng**. Sửa chữ thì câu báo "Cần tạo lại giọng".
3. **Rút gọn câu quá dài (Claude)**: nhờ Claude rút gọn các câu "Quá dài" cho vừa khung thời gian (dựa trên tốc độ đọc thật của giọng đó), rồi tự tạo lại giọng cho các câu đó. Cần API key Claude.
4. Bước 4, nhóm **Lồng tiếng**: bật "Thêm giọng lồng tiếng", chọn xử lý tiếng gốc (tự nhỏ đi khi có giọng / nhỏ cố định / tắt hẳn / giữ nguyên), âm lượng, tăng tốc tối đa; rồi xuất video.

Giọng đọc mặc định là **Edge TTS** (`pip install edge-tts`, Colab tự cài): miễn phí, giọng tiếng Việt Hoài My / Nam Minh, nhưng cần mạng và là dịch vụ **không chính thức** của Microsoft nên có thể bị chặn hoặc đổi. Muốn dùng nguồn giọng khác (Azure, ElevenLabs, OpenAI, FPT.AI...) chỉ cần viết một lớp có `synth(text, voice, out_path)` trong `studio/tts.py`.

## 12. Máy nhà + Tailscale (miễn phí, không bị ngắt như Colab)

Chạy app trên laptop/PC ở nhà, iPad hoặc điện thoại kết nối qua **Tailscale** (mạng riêng ảo, bản cá nhân miễn phí; bạn tự xem điều kiện hiện tại tại tailscale.com). Địa chỉ **cố định**, không đổi mỗi lần như tunnel Colab, không có giới hạn tải lên 100 MB của Cloudflare, và chỉ thiết bị trong mạng Tailscale của bạn mới vào được.

**Làm một lần trên laptop Windows:**
1. Cài **Tailscale** (tailscale.com/download), đăng nhập, bật Connected.
2. Chạy `setup.bat` để cài Python, ffmpeg, **Tesseract** (cần cho OCR; có thể hiện hộp xin quyền quản trị, hãy bấm Yes) và dữ liệu ngôn ngữ. Nếu Tesseract vẫn chưa cài được, xem README mục "Xử lý lỗi thường gặp".
3. (Tuỳ chọn) Sao chép `local.env.example` thành `local.env`, sửa `MEDIA_DIR` thành thư mục chứa video của bạn (ví dụ `D:/Videos`) để chọn video có sẵn trong app mà không cần tải lên. Cài thêm Whisper nếu muốn: `.venv\Scripts\pip install -r requirements-asr.txt`.
4. Chạy **`start_tailscale.bat`**. Nó tự lấy địa chỉ Tailscale của máy, tự tạo mã truy cập (lưu ở `access_token.txt`), tự chạy lại nếu app dừng đột ngột, rồi in ra một link dạng `http://100.x.y.z:8765/?token=...` (cũng lưu ở `link.txt`).
5. (Tuỳ chọn) Chạy **`install_autostart.bat`**: app tự chạy mỗi lần bạn đăng nhập Windows, và máy không tự ngủ khi cắm sạc.

**Trên iPad/điện thoại:** cài app **Tailscale** (App Store), đăng nhập **cùng tài khoản**, bật kết nối, rồi mở link ở bước 4. Lưu link vào màn hình chính để dùng lần sau.

Lưu ý:
- Máy nhà phải **đang bật, không ngủ và có mạng** khi bạn dùng. Đóng nắp laptop thường làm máy ngủ: đổi bằng `powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0` rồi `powercfg /setactive SCHEME_CURRENT` (khi cắm sạc đóng nắp sẽ không ngủ; chú ý nhiệt độ).
- App chỉ lắng nghe trên địa chỉ Tailscale, không mở ra mạng nhà hay Internet. Vẫn đừng chia sẻ link vì nó chứa mã truy cập.
- Muốn có địa chỉ **https** (một số tính năng của trình duyệt chỉ chạy trên https, ví dụ nút Chia sẻ): bật HTTPS trong trang quản trị Tailscale rồi dùng `tailscale serve` (cú pháp lệnh khác nhau giữa các phiên bản, xem `tailscale serve --help`).
- `start_tailscale.bat` và `install_autostart.bat` chưa được thử trên Windows thật: nếu lỗi, bạn chụp màn hình cửa sổ lệnh gửi lại.
