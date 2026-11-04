FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim tesseract-ocr-chi-tra tesseract-ocr-kor tesseract-ocr-jpn \
    && rm -rf /var/lib/apt/lists/*
# chạy bằng user thường (Hugging Face Spaces yêu cầu uid 1000)
RUN useradd -m -u 1000 user
WORKDIR /app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user . .
RUN mkdir -p /app/projects && chown -R user /app
USER user
ENV HOST=0.0.0.0 PORT=8765 NO_BROWSER=1
EXPOSE 8765
CMD ["python", "app.py"]
