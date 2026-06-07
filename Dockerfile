FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860 \
    DATA_DIR=/app/asset \
    IMAGE_DIR=/app/static/card \
    WEB_CONCURRENCY=1 \
    WEB_THREADS=8 \
    WEB_TIMEOUT=120

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY guess_card_game.py data_utils.py card_build.py map.py ./
COPY asset ./asset_seed
COPY templates ./templates
COPY static ./static

RUN mkdir -p /app/asset /app/static/card

EXPOSE 7860

CMD ["sh", "-c", "mkdir -p \"${DATA_DIR}\" \"${IMAGE_DIR}\"; [ -f \"${DATA_DIR}/cards.cdb\" ] || cp /app/asset_seed/cards.cdb \"${DATA_DIR}/cards.cdb\"; [ -f \"${DATA_DIR}/cards.cdb.md5\" ] || cp /app/asset_seed/cards.cdb.md5 \"${DATA_DIR}/cards.cdb.md5\"; [ -f \"${DATA_DIR}/strings.conf\" ] || cp /app/asset_seed/strings.conf \"${DATA_DIR}/strings.conf\"; gunicorn --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY} --threads ${WEB_THREADS} --timeout ${WEB_TIMEOUT} guess_card_game:app"]
