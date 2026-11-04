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
    && useradd --uid 10002 --gid broker --no-create-home broker \
    && mkdir -p /run/pprl && chown 10002:101 /run/pprl && chmod 770 /run/pprl
COPY backend/src/broker ./src/broker
COPY backend/src/__init__.py ./src/__init__.py
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
COPY --from=dashboard /build/dist /app/dashboard
USER 10002:101
CMD ["sh", "-c", "python -m alembic upgrade head && exec python -m uvicorn src.broker.api:create_app --factory --uds /run/pprl/broker.sock --workers 1 --no-access-log"]
