FROM python:3.12.8-slim-bullseye

WORKDIR /app

# 安裝系統依賴
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安裝最新版 Poetry
ENV POETRY_HOME=/opt/poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="${POETRY_HOME}/bin:${PATH}"

# 設定 Poetry 不創建虛擬環境
RUN poetry config virtualenvs.create false

# 複製應用程式程式碼
COPY . .

# 安裝依賴，使用 --no-root 選項
RUN poetry install --no-root --no-dev

# 設定環境變數
ENV PYTHONUNBUFFERED=1

# 執行應用程式
CMD ["python", "main.py"]