# Bauform eines anderen eigenen Dienstes. Basis über den DIGEST gepinnt, nicht
# über den Tag; Digest dort am 23.08.2026 gemessen, hier beim ersten Bau
# gegengeprüft. Eine Abhängigkeit seit 26.09.2026: Babel für die Schreibweisen nach CLDR (app/format.py), gepinnt mit Hash in
# requirements.txt (babel 2.18.0, BSD-3-Clause, ohne weitere Abhängigkeiten, rund 33 MB; im Basisbild gemessen installierbar).
FROM python:3.13-slim-trixie@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a

# TZ allein ist kein Beleg: die Einteilung nach Tag/Woche nutzt SQLite 'localtime',
# und das braucht /usr/share/zoneinfo/Europe/Zurich im Bild. Gemessen wird nach dem
# Bau mit strftime('%G-W%V','2026-01-04T23:30:00Z','localtime'): Zürich -> 2026-W02,
# UTC wäre 2026-W01. Gemessen am 2026-09-02 im Bild 1b6293a: 2026-W02, tzdata vorhanden.
# TZ steht NICHT im Bild: die Zone gehört dem Betreiber, nicht dem Autor (Codex-Befund 20.09.2026). Sie kommt
# aus compose.yaml und muss dort gesetzt sein. tzdata bleibt im Bild, sonst fällt SQLites 'localtime' auf UTC.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GITPULS_DB=/data/gitpuls.db

WORKDIR /app

# Namentliche COPY-Zeilen statt COPY . — deploy/ausrollprobe.sh liest die Pfade
# aus genau diesen Zeilen.
COPY requirements.txt ./
RUN pip install --no-cache-dir --disable-pip-version-check --root-user-action=ignore --require-hashes -r requirements.txt
COPY app/ ./app/

RUN groupadd -g 10001 dienst \
 && useradd -u 10001 -g 10001 --no-create-home --shell /usr/sbin/nologin dienst \
 && mkdir -p /data /etc/gitpuls && chown 10001:10001 /data && chmod 0750 /data

# Version des Release-Bilds (v1.0.2): release.yml übergibt sie als Build-Argument, die Fusszeile liest sie vor GITPULS_TAG. Ohne Argument
# (Bau aus dem Checkout) bleibt sie leer, und die Fusszeile zeigt wie bisher GITPULS_TAG.
ARG GITPULS_VERSION=""
ENV GITPULS_VERSION=$GITPULS_VERSION
LABEL org.opencontainers.image.source="https://github.com/cidix/gitpuls" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="$GITPULS_VERSION" \
      org.opencontainers.image.description="GitHub activity and server state dashboard"

USER 10001:10001
EXPOSE 8789
# Derselbe Healthcheck wie in compose.example.yaml, damit das Image auch ohne Compose seinen Zustand meldet (A15).
HEALTHCHECK --interval=30s --timeout=10s --retries=3 CMD ["python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8789/gesund',timeout=5)"]

# Bindet IM CONTAINER auf 0.0.0.0, veröffentlicht wird 127.0.0.1:8789 (compose.yaml).
CMD ["python", "-m", "app.web"]
