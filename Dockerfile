FROM python:3.12-slim-bookworm

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency installation
RUN pip install --no-cache-dir uv

# Copy all project files (Railway automatically respects .gitignore, so exe/secrets are ignored)
COPY . .

# Install the project and optional dependencies
RUN uv pip install --system ".[inference-cloud,inference-google,channel-telegram,channel-gmail]"

# Command to run the telegram bot
CMD ["python", "examples/telegram_agent_system.py"]
