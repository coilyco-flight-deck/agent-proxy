FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set working directory  
WORKDIR /app

# Copy and install Python dependencies using uv
COPY pyproject.toml .
RUN pip install --no-cache-dir uv && \
    uv sync --extra dev

# Copy application code
COPY . .

# Make sure the virtual environment's bin is in PATH
ENV PATH="/app/.venv/bin:$PATH"

# Expose the proxy port
EXPOSE 8080

# Define entrypoint for service  
CMD ["python", "-m", "app.main"]