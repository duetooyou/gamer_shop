FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[dev]"

COPY alembic.ini ./
COPY data ./data
COPY scripts ./scripts
COPY stub_supplier ./stub_supplier
COPY tests ./tests

CMD ["granian", "--interface", "asgi", "gamer_shop.main:app", "--host", "0.0.0.0", "--port", "8000"]
