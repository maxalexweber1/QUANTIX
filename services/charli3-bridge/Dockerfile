FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY charli3_bridge ./charli3_bridge

RUN pip install --no-cache-dir -e .

EXPOSE 8000
CMD ["uvicorn", "charli3_bridge.main:app", "--host", "0.0.0.0", "--port", "8000"]
