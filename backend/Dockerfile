FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Shell form on purpose: Railway, Render and Fly all assign the port at runtime
# via $PORT and expect the process to bind it. Exec form cannot expand a
# variable, so a hardcoded 8000 makes the platform's health check fail against a
# port nothing is listening on — and the only symptom is a deploy that never
# turns green. Falls back to 8000 so docker compose is unchanged.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
