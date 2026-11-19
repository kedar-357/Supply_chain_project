#!/bin/sh
set -e
# Initialise the file-based data store on first start (no database needed)
if [ "$SEED_TRAIN" = "true" ]; then
  python -m app.seed.init_data --train-if-untrained
else
  python -m app.seed.init_data
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
