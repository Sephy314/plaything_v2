FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Minecraft 26.1+ requires Java 25.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-25-jre-headless \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml alembic.ini ./
COPY . .
RUN pip install --upgrade pip && pip install .


EXPOSE 8080

CMD ["python", "-m", "bot.main"]