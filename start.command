#!/usr/bin/env bash
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Không tìm thấy python3. Hãy cài Python 3.10 trở lên."; exit 1
fi
if [ ! -d .venv ]; then
  echo "Lần đầu chạy: đang tạo môi trường và cài thư viện, vui lòng đợi..."
  python3 -m venv .venv && . .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt
else
  . .venv/bin/activate
fi
python app.py
