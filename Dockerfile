FROM python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

ARG SKIP_IMAGES=0
RUN if [ "$SKIP_IMAGES" = "1" ]; then \
        python card_build.py --skip-images; \
    else \
        python card_build.py; \
    fi

ENTRYPOINT ["python"]
CMD ["guess_card_game.py"]
