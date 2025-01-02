# Build stage
FROM alpine:3.19 AS builder

# Install build dependencies
RUN apk add --no-cache \
    python3~=3.11 \
    python3-dev \
    py3-pip \
    gcc \
    musl-dev \
    linux-headers \
    yaml-dev

WORKDIR /app

# Create virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only requirements file
COPY requirements.txt .

# Install dependencies in venv
RUN pip install --no-cache-dir -r requirements.txt

# Runtime stage
FROM alpine:3.19 AS runtime

# Install runtime dependencies
RUN apk add --no-cache \
    python3~=3.11 \
    ffmpeg \
    ca-certificates \
    yaml \
    tzdata

WORKDIR /app

# Copy venv from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the application
CMD ["python", "main.py"]