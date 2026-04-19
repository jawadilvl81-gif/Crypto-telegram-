FROM python:3.10-slim

# Install system dependencies for matplotlib/pandas
RUN apt-get update && apt-get install -y \
    build-essential \
    libatlas-base-dev \
    gfortran \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy files
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directory for bot state
RUN mkdir -p /app/data

# Environment variables (optional, secrets better hote hain)
ENV PYTHONUNBUFFERED=1

# Run the bot
CMD ["python", "main.py"]
