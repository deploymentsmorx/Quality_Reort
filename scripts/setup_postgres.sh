#!/bin/sh
# Run on the Lightsail server (Ubuntu) to create the inspection database.
# Usage: sudo POSTGRES_PASSWORD='a-long-secret' sh scripts/setup_postgres.sh

set -eu
DB_NAME="${DB_NAME:-ie_inspection}"
DB_USER="${DB_USER:-ie}"
DB_PASSWORD="${POSTGRES_PASSWORD:-}"

if [ -z "$DB_PASSWORD" ]; then
  echo "Set POSTGRES_PASSWORD first." >&2
  exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
  apt-get update
  apt-get install -y postgresql postgresql-contrib
  systemctl enable --now postgresql
fi

su - postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'\"" | grep -q 1 \
  || su - postgres -c "psql -c \"CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';\""

su - postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'\"" | grep -q 1 \
  || su - postgres -c "psql -c \"CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};\""

echo "Database ${DB_NAME} is ready."
echo "DATABASE_URL=postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:5432/${DB_NAME}"
