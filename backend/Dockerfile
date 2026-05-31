FROM python:3.9-slim

WORKDIR /app

# System dependencies:
#   libgl1 / libglib2.0-0 — OpenCV runtime shared libs
#   poppler-utils        — pdf2image backend (pdftoppm) for PDF ingestion
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Install the CPU-only PyTorch build first so EasyOCR reuses it instead of pulling
# the default CUDA wheel (~2-3GB of nvidia-* packages this CPU-only deploy never uses).
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code from backend directory
COPY backend/ .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
