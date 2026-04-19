FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY supplier_erp_mock ./supplier_erp_mock

RUN pip install --no-cache-dir -e .

EXPOSE 8001
CMD ["uvicorn", "supplier_erp_mock.main:app", "--host", "0.0.0.0", "--port", "8001"]
