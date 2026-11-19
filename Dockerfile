FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tzdata fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY tapo_webcam.py tapo_cloud_auth.py tapo_faces.py ./
COPY models ./models
ENV TAPO_WEBCAM_CONFIG=/config/config.toml TZ=Europe/Berlin
EXPOSE 8099
VOLUME ["/data"]
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8099/health')"
CMD ["python", "tapo_webcam.py", "serve"]
