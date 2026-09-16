# Single image for the app service, the OIDC provider, and the worker pool --
# only the container command differs (see docker-compose.yml). Keeps blue and
# green byte-for-byte identical, which is the whole point of blue/green.
FROM python:3.12-slim

RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY worker ./worker

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
