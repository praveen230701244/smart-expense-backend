# Use lightweight Python image
FROM python:3.10-slim

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=5000 \
    TESSERACT_CMD=/usr/bin/tesseract \
    USE_MINILM=false \
    CORS_ORIGIN=* \
    FIREBASE_PROJECT_ID="" \
    GEMINI_API_KEY=""

# Set working directory
WORKDIR /app

# Install system dependencies (Tesseract + build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libtesseract-dev \
    gcc \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for caching)
COPY requirements.txt requirements-ml.txt ./

# Install Python dependencies
ARG INSTALL_ML=false
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt && \
    if [ "$INSTALL_ML" = "true" ]; then pip install -r requirements-ml.txt; fi

# Copy project files
COPY . .

# Expose port
EXPOSE 5000

# Start server
CMD ["gunicorn", "-w", "2", "-k", "gthread", "--threads", "4", "-b", "0.0.0.0:5000", "app:app"]