FROM python:3.11-bookworm

# Install system dependencies for PaddleOCR and OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download and extract PaddleOCR models using curl/tar to avoid runtime latency
RUN mkdir -p /root/.paddleocr/whl/det/en/ && \
    mkdir -p /root/.paddleocr/whl/rec/en/ && \
    mkdir -p /root/.paddleocr/whl/cls/ && \
    curl -L https://paddleocr.bj.bcebos.com/PP-OCRv3/english/en_PP-OCRv3_det_infer.tar -o /root/.paddleocr/whl/det/en/en_PP-OCRv3_det_infer.tar && \
    tar -xf /root/.paddleocr/whl/det/en/en_PP-OCRv3_det_infer.tar -C /root/.paddleocr/whl/det/en/ && \
    curl -L https://paddleocr.bj.bcebos.com/PP-OCRv4/english/en_PP-OCRv4_rec_infer.tar -o /root/.paddleocr/whl/rec/en/en_PP-OCRv4_rec_infer.tar && \
    tar -xf /root/.paddleocr/whl/rec/en/en_PP-OCRv4_rec_infer.tar -C /root/.paddleocr/whl/rec/en/ && \
    curl -L https://paddleocr.bj.bcebos.com/dygraph_v2.0/ch/ch_ppocr_mobile_v2.0_cls_infer.tar -o /root/.paddleocr/whl/cls/ch_ppocr_mobile_v2.0_cls_infer.tar && \
    tar -xf /root/.paddleocr/whl/cls/ch_ppocr_mobile_v2.0_cls_infer.tar -C /root/.paddleocr/whl/cls/

# Copy service code
COPY main.py .

EXPOSE 8757

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8757"]
