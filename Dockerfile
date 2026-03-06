FROM python:3.11-slim

# Create app directory
WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY requirements.txt /app/requirements.txt

# Install requirements
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy app code
COPY . /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose port
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
