FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# One image, three roles — docker-compose.yml overrides this `command` per
# service (api / worker / local S3-trigger poller).
CMD ["python", "main.py"]
