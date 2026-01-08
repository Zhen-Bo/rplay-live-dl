# Build stage
FROM python:3.11-alpine AS builder

# Install build dependencies
RUN apk add --no-cache \
    gcc \
    musl-dev \
    linux-headers \
    yaml-dev

WORKDIR /app

# Install Poetry
RUN pip install --no-cache-dir poetry

# Copy dependency files
COPY pyproject.toml poetry.lock ./

# Configure Poetry: no virtualenv, install to system
RUN poetry config virtualenvs.create false && \
    poetry install --only main --no-interaction --no-ansi

# Runtime stage
FROM python:3.11-alpine AS runtime

# Install runtime dependencies
RUN apk add --no-cache \
    ffmpeg \
    ca-certificates \
    yaml \
    tzdata

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the application
CMD ["python", "main.py"]
