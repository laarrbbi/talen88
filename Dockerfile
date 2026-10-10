# Stage 1: build React frontend
FROM node:20-alpine AS frontend-build
WORKDIR /build
COPY app/package*.json ./
RUN npm ci
COPY app/ ./
# Empty VITE_ URLs → same-origin relative paths (data API serves the SPA)
RUN VITE_DATA_URL="" VITE_AGENTS_URL="" npm run build

# Stage 2: Python runtime
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends supervisor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies (shared across all three services)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application source
COPY data/ data/
COPY agents/ agents/
COPY model_service/ model_service/
COPY llm/ llm/

# React SPA (built in stage 1)
COPY --from=frontend-build /build/dist app/dist/

# Deployment helpers
COPY deploy/ deploy/
RUN chmod +x deploy/start.sh

EXPOSE 8080
CMD ["./deploy/start.sh"]
