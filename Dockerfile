FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml constraints.txt ./
COPY src ./src
RUN pip install --no-cache-dir -c constraints.txt .
COPY alembic.ini ./
COPY migrations ./migrations
RUN useradd --create-home appuser
USER appuser
EXPOSE 8000
CMD ["uvicorn", "football_analytics.main:app", "--host", "0.0.0.0", "--port", "8000"]
