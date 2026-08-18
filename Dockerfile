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

# Pre-download and extract PaddleOCR models using curl/tar to avoid build-time python execution segfaults
RUN mkdir -p /root/.paddleocr/whl/det/en/ && \
    mkdir -p /root/.paddleocr/whl/rec/en/ && \
    mkdir -p /root/.paddleocr/whl/cls/ && \
    curl -L https://paddleocr.bj.bcebos.com/PP-OCRv3/english/en_PP-OCRv3_det_infer.tar -o /root/.paddleocr/whl/det/en/en_PP-OCRv3_det_infer.tar && \
    tar -xf /root/.paddleocr/whl/det/en/en_PP-OCRv3_det_infer.tar -C /root/.paddleocr/whl/det/en/ && \
    curl -L https://paddleocr.bj.bcebos.com/PP-OCRv4/english/en_PP-OCRv4_rec_infer.tar -o /root/.paddleocr/whl/rec/en/en_PP-OCRv4_rec_infer.tar && \
    tar -xf /root/.paddleocr/whl/rec/en/en_PP-OCRv4_rec_infer.tar -C /root/.paddleocr/whl/rec/en/ && \
    curl -L https://paddleocr.bj.bcebos.com/dygraph_v2.0/ch/ch_ppocr_mobile_v2.0_cls_infer.tar -o /root/.paddleocr/whl/cls/ch_ppocr_mobile_v2.0_cls_infer.tar && \
    tar -xf /root/.paddleocr/whl/cls/ch_ppocr_mobile_v2.0_cls_infer.tar -C /root/.paddleocr/whl/cls/

# Copy application source code
COPY . .

# Expose server port
EXPOSE 8756

# Run in unbuffered mode to flush logs instantly in container output
CMD ["python", "-u", "server.py"]
