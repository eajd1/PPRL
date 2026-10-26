FROM node:22-alpine AS dashboard
WORKDIR /build
COPY broker-dashboard/package.json broker-dashboard/package-lock.json ./
RUN npm ci
COPY broker-dashboard/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/broker-requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10002 broker \
    && useradd --uid 10002 --gid broker --no-create-home broker
COPY backend/src/broker ./src/broker
COPY backend/src/__init__.py ./src/__init__.py
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
COPY --from=dashboard /build/dist /app/dashboard
USER broker
EXPOSE 8000
CMD ["sh", "-c", "python -m alembic upgrade head && exec python -m uvicorn src.broker.api:create_app --factory --host 0.0.0.0 --port 8000 --workers 1 --no-access-log"]
