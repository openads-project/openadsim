FROM python:3.11-slim

WORKDIR /app

# Install system dependencies used by runtime control, health checks, and optional X11 browser launch.
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium curl \
    && useradd --create-home --shell /bin/sh openadsim \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies. Streamlit 1.61.0 is incompatible with the
# breaking GZipResponder signature introduced in Starlette 1.4.0.
RUN pip install --no-cache-dir \
    "streamlit==1.61.0" \
    "starlette==1.3.1" \
    "pydantic>=2.6.0,<3"

# Copy Configuration GUI application
COPY gui.py \
     models.py \
     checks.py \
     presets.py \
     utils.py \
     control.py \
     start.sh \
     ./
COPY assets ./assets
COPY .streamlit ./.streamlit
RUN chmod +x /app/start.sh

# Expose Streamlit port
EXPOSE 8501

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# Run Streamlit
CMD ["/app/start.sh"]
