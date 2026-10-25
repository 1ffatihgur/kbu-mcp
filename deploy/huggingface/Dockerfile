# Hugging Face Space için: paketi GitHub sürümünden kurar (Space deposunda kaynak kod tutulmaz).
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-tur \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    KBU_DATA_DIR=/home/user/data \
    KBU_TRANSPORT=http \
    PORT=7860 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

ARG KBU_VERSION=v1.1.0
RUN pip install --no-cache-dir --user "kbu-mcp[remote] @ https://github.com/1ffatihgur/kbu-mcp/archive/refs/tags/${KBU_VERSION}.zip"

EXPOSE 7860
CMD ["kbu-mcp", "--http"]
