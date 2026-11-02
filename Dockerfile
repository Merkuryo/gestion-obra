FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git y make para trabajar dentro del contenedor; postgresql-client para psql y copias.
# Para generar PDF con WeasyPrint hará falta además: libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b
RUN apt-get update \
    && apt-get install -y --no-install-recommends git make postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Usuario con el mismo UID que tu usuario de WSL (normalmente 1000):
# así los ficheros creados en el contenedor son tuyos en WSL.
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} app && useradd -m -u ${UID} -g ${GID} -s /bin/bash app

WORKDIR /app
COPY requirements/ requirements/
RUN pip install -r requirements/dev.txt
COPY --chown=app:app . .

USER app
EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
