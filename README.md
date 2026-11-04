# Video localization

Web app chạy trên máy của bạn: từ một video có sub cứng (Trung, Anh...) làm ra video sub tiếng Việt, có logo, watermark, và xuất đúng chuẩn đăng YouTube.

Luồng làm việc gồm 4 bước:

1. **Video**: chọn file hoặc dán đường dẫn.
2. **Quét sub**: tự dò vùng sub cứng, chọn ngôn ngữ từng dòng, quét OCR ra danh sách câu có thời gian.
3. **Phụ đề**: sửa lỗi OCR, dịch bằng Claude (hoặc nhập SRT dịch sẵn), xuất SRT.
4. **Xuất video**: xoá sub cũ (tô lại nền rồi làm nhoè), thêm logo, chữ watermark góc, chữ watermark bay vòng, ghi sub Việt đúng chỗ sub cũ, xuất MP4 chuẩn YouTube và kiểm tra lại file.

## 1. Cài đặt (làm một lần)

Cần 3 thứ ngoài Python: **ffmpeg** (bản có libass), **Tesseract OCR** và **Python 3.10+**.

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
