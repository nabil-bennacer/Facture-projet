FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 PIP_NO_CACHE_DIR=1
WORKDIR /app

# Dépendances d'abord : cette couche reste en cache tant que requirements.txt ne change pas
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY facture/ facture/
COPY eval/ eval/
COPY tests/ tests/

# Les données (landing, bronze, silver, entrepôt) vivent dans un volume, pas dans l'image.
# Les secrets arrivent par --env-file .env, jamais dans l'image.
VOLUME ["/app/data"]
ENTRYPOINT ["python", "-m", "facture"]
