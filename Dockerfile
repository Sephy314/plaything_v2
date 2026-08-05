FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml alembic.ini ./
RUN pip install --upgrade pip && pip install .

COPY . .

EXPOSE 8080

CMD ["python", "-m", "bot.main"]