FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv via pip
RUN pip install uv

# Copy project files
COPY pyproject.toml .
COPY requirements.txt .

# Install Python dependencies with uv
RUN uv pip install --system --no-cache -r requirements.txt

# Copy application code
COPY . .

# Create directory for database
RUN mkdir -p /app/data

# Set environment variables
ENV PYTHONPATH="/app"
ENV DB_PATH="/app/data/fixtures.db"
ENV PORT=8000

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Run the application
CMD ["python", "api.py"]