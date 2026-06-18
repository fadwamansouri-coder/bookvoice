FROM python:3.11-slim

WORKDIR /app

# Install system deps for PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway sets PORT env var
ENV PORT=8080
EXPOSE 8080

CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120 --threads 4
