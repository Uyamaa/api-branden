FROM python:3.11-slim
LABEL org.opencontainers.image.source="https://github.com/Uyamaa/api-branden"
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY consumer.py producer.py ./
RUN useradd --no-create-home --uid 10001 appuser
USER appuser
# Healthy = the loop has run in the last 90 seconds (it touches this file on every poll, even when idle)
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import os,sys,time; sys.exit(0 if time.time()-os.path.getmtime('/tmp/consumer-heartbeat')<90 else 1)"
CMD ["python", "consumer.py"]
