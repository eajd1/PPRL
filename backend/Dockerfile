FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 pprl \
    && useradd --uid 10001 --gid pprl --no-create-home pprl \
    && mkdir -p /app/runtime \
    && chown pprl:pprl /app/runtime
COPY backend/app ./app
COPY backend/src ./src
COPY backend/dataset ./dataset
USER pprl
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
