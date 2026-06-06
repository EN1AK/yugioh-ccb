FROM python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

ARG SKIP_PACK_INFO=0
ARG SKIP_IMAGES=0
RUN extra_args=""; \
    if [ "$SKIP_PACK_INFO" = "1" ]; then extra_args="$extra_args --skip-pack-info"; fi; \
    if [ "$SKIP_IMAGES" = "1" ]; then extra_args="$extra_args --skip-images"; fi; \
    python card_build.py $extra_args

ENTRYPOINT ["python"]
CMD ["guess_card_game.py"]
