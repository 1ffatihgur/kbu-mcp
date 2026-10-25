# KBÜ MCP uzak sunucusu (Streamable HTTP). Hugging Face Spaces, Render, Koyeb, kendi sunucunuz vb.
#   docker build -t kbu-mcp .
#   docker run -p 7860:7860 -v kbu-data:/home/user/data kbu-mcp
FROM python:3.12-slim

# OCR: Tesseract + Türkçe model (taranmış PDF'ler için)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-tur \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces kapsayıcıyı 1000 numaralı kullanıcıyla çalıştırır
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    KBU_DATA_DIR=/home/user/data \
    KBU_TRANSPORT=http \
    PORT=7860 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /home/user/app
COPY --chown=user pyproject.toml README.md LICENSE ./
COPY --chown=user src ./src
RUN pip install --no-cache-dir --user ".[remote]"

EXPOSE 7860
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"7860\")}/health',timeout=8)"
CMD ["kbu-mcp", "--http"]
