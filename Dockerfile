FROM python:3.10-slim

WORKDIR /app

# 安装系统依赖（matplotlib 需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libfreetype6-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 创建数据目录用于保存状态和历史记录
RUN mkdir -p /app/data

# 运行 watchdog 主进程
CMD ["python", "watchdog.py"]
