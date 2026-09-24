# Use the official Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Application Code
COPY . .

# Create and Switch to Non-Root User
RUN useradd -m -u 1000 user

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

ENV PYTHONUNBUFFERED=1

# 6. Run the App
EXPOSE 7860
# Single worker (the app keeps per-user progress/log/task state in plain
CMD ["gunicorn", "--bind", "0.0.0.0:7860", "--workers", "1", "--threads", "8", "--timeout", "120", "app:app"]