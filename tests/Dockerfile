FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssl nodejs \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt /tmp/hospital.txt
COPY broker/requirements.txt /tmp/broker.txt
RUN pip install --no-cache-dir -r /tmp/hospital.txt -r /tmp/broker.txt httpx==0.28.1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/workspace/backend:/workspace
WORKDIR /workspace
CMD ["python", "-m", "unittest", "-v", "tests.test_broker", "tests.test_mtls"]
