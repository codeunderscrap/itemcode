FROM python:3.12-bookworm

# Install system dependencies for PaddleOCR, PyMuPDF, and other libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set up working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose server port
EXPOSE 8756

# Run in unbuffered mode to flush logs instantly in container output
CMD ["python", "-u", "server.py"]
