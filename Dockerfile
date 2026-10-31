# ═══════════════════════════════════════════════════════════════════════
# Praj.AI Onboarding Converter — Dockerfile
#
# The converter microservice (service/) wrapping the raw→Golden engine (engine/).
# PRAJ_IQ calls it to turn a customer's raw POS/accounting exports into the Golden
# Standard workbook. Runs on port 8100 (PRAJ_IQ's own backend owns 8000).
#
#   docker build -t praj-converter .
#   docker run -p 8100:8100 praj-converter
# ═══════════════════════════════════════════════════════════════════════

FROM python:3.11-slim

# curl is only needed for the container HEALTHCHECK. pandas/numpy/openpyxl install
# from manylinux wheels, so no compiler/system libs are required.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install Python deps first (engine + service) for layer caching.
COPY requirements.txt ./requirements.txt
COPY service/requirements.txt ./service-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r service-requirements.txt

# Copy only what the service needs at runtime: the generic engine, its mapping/
# profile YAML packs (inside engine/), and the FastAPI service.
COPY engine/ ./engine/
COPY service/ ./service/

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=10s \
    CMD curl -f http://localhost:8100/health || exit 1

# No --reload in the image. Two workers give some concurrency for the
# (CPU-bound) pandas conversion on a single host.
CMD ["uvicorn", "service.app:app", \
     "--host", "0.0.0.0", \
     "--port", "8100", \
     "--workers", "2", \
     "--timeout-keep-alive", "120", \
     "--log-level", "info"]
