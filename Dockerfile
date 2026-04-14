FROM python:3.12-slim

# Install Node.js 20
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

WORKDIR /app

# Copy and install Python dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

# Copy and build frontend
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN npm install --prefix frontend
COPY frontend ./frontend
RUN npm run build --prefix frontend

# Copy backend
COPY tokenwise ./tokenwise
COPY .env.example .env.example

EXPOSE 8000

CMD ["sh", "-c", "uv run uvicorn tokenwise.backend.main:app --host 0.0.0.0 --port $PORT"]
