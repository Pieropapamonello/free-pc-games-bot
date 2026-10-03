FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    PORT=10000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py trailers.py nello_downloader.py ./

RUN mkdir -p /data && chmod -R 777 /data

EXPOSE 10000

CMD ["python", "-u", "bot.py"]
