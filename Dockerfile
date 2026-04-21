FROM python:3.13-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create logs directory
RUN mkdir -p logs

# Cloud Run requires binding to 0.0.0.0
ENV API_HOST=0.0.0.0

# Run the API
CMD ["python", "main.py"]
