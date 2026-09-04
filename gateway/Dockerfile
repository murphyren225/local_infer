FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY tandem_gateway ./tandem_gateway
RUN pip install --no-cache-dir .

EXPOSE 8080
CMD ["uvicorn", "tandem_gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]
