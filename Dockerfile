# Use Playwright's official Python image as the base image
# This pre-installs Chromium, Firefox, WebKit and system-level dependencies.
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

# Switch to root to install dependencies and allow gosu to step down privileges in the entrypoint
USER root

# Set working directory
WORKDIR /app

# Install uv inside the build layer
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY requirements.txt .

# Install gosu and run uv
RUN apt-get update && \
    apt-get install -y gosu ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    uv pip install --system --no-cache -r requirements.txt && \
    rm requirements.txt

# Copy entrypoint
COPY entrypoint.sh /entrypoint.sh

# Make entrypoint executable
RUN chmod +x /entrypoint.sh

# Supress pkg_resources deprecation warning until upstream resolves
# ENV PYTHONWARNINGS="ignore:pkg_resources is deprecated as an API"

# command to run the app (will accept arguments)
ENTRYPOINT ["/entrypoint.sh"]
# Default is interactive menus.
# Alternately, --idle will run the permissions fixing, then just wait for docker exec to run the app.
CMD []
