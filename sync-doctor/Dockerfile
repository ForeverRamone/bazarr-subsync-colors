FROM python:3.12-slim-bookworm

ARG ALASS_VERSION=v2.0.0
ARG ALASS_SHA256=7bd0b9ae7e035d3ba940eacffb21243614df36231d47f21f0b4ce42001ab7fcd

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl tzdata \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /usr/local/bin/alass-cli \
      "https://github.com/kaegi/alass/releases/download/${ALASS_VERSION}/alass-linux64" \
 && echo "${ALASS_SHA256}  /usr/local/bin/alass-cli" | sha256sum -c - \
 && chmod +x /usr/local/bin/alass-cli

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY doctor /app/doctor
WORKDIR /app

ENV DATA_DIR=/data \
    PORT=8686 \
    PYTHONUNBUFFERED=1

EXPOSE 8686
VOLUME ["/data"]
HEALTHCHECK --interval=5m --timeout=10s CMD curl -fs http://localhost:8686/health || exit 1

CMD ["python", "-m", "doctor"]
