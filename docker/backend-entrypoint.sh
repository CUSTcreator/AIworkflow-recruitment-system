#!/bin/sh
set -eu

python -m alembic -c backend/alembic.ini upgrade head
python -m backend.app.seeds.import_university_rankings
exec "$@"
