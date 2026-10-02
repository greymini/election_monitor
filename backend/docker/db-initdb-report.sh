#!/bin/bash
# Report which of the extensions 0001 needs are available, on first boot only.
#
# Deliberately does NOT create them. db/apply_migrations.py creates them through
# 0001_extensions.sql, so the migration ledger records the fact and a database
# restored from a dump takes the same path as a fresh one. An init script that
# created them would make the ledger lie about a clean install.
set -euo pipefail

echo "checking the extensions 0001_extensions.sql requires:"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
	SELECT name, default_version,
	       CASE WHEN installed_version IS NULL THEN 'available, not yet created'
	            ELSE 'created' END AS state
	  FROM pg_available_extensions
	 WHERE name IN ('postgis', 'vector', 'pg_trgm', 'unaccent')
	 ORDER BY name;
SQL
