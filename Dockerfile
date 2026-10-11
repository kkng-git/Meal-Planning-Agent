FROM python:3.13-slim AS build

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates nodejs npm \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /install /usr/local

WORKDIR /app
COPY agent.py main.py ./

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080
CMD ["python", "main.py"]
