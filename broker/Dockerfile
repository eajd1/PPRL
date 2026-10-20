FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY broker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && mkdir -p /data /run/pprl \
    && chown -R 10001:101 /data /run/pprl \
    && chmod 770 /run/pprl
COPY broker/app ./app
USER 10001:101
CMD ["python", "-m", "uvicorn", "app.main:app", "--uds", "/run/pprl/broker.sock", "--workers", "1", "--no-access-log"]
