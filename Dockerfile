# KBÜ MCP — üretim imajı (MCP sunucusu ve tarama işçisi aynı imajı kullanır; bkz. docker-compose.yml)
FROM python:3.11-slim

# Tesseract: taranmış PDF'ler için OCR (Türkçe + İngilizce)
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-tur tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 KBU_DATA_DIR=/data
VOLUME /data
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/health')" || exit 1
CMD ["kbu", "serve", "--http", "--port", "7860"]
