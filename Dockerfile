FROM python:3.12-slim

# Synology: first user is usually uid 1026, group "users" is gid 100.
# Build with --build-arg APP_UID=... so the audit folder on the host is writable
# without changing any file owner or mode.
ARG APP_UID=1026
ARG APP_GID=100

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home --shell /usr/sbin/nologin nastasker

USER ${APP_UID}:${APP_GID}
CMD ["python", "-m", "nas_tasker"]
