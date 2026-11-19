"""Reproducible initialisation of the file-based data store.

Usage (from backend/):
    python -m app.seed.init_data                 # create sample data (if missing) and load it
    python -m app.seed.init_data --train         # ... and train forecasting + delay models (~3 min)
    python -m app.seed.init_data --reset         # archive the current data/ folder and start fresh
    python -m app.seed.init_data --regenerate    # regenerate the sample CSVs (dates end yesterday)
    python -m app.seed.init_data --train-if-untrained  # train only when no model exists yet

Steps
1. ``datasets/sample/*.csv`` — deterministic synthetic sample datasets (seed 42). These originals are
   never modified by the application.
2. Each sample file is validated and imported into the working store ``data/*.csv`` through the same
   pipeline as Data Management uploads.
3. ``data/settings.json`` gets default business parameters and ``data/users.json`` the demo accounts
   (passwords from DEMO_*_PASSWORD in .env).
"""
from __future__ import annotations

import argparse
import logging
import shutil
import time
from datetime import datetime

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.security import RoleName
from app.seed.synthetic import generate
from app.services import settings_service, user_service
from app.services.activity import log_activity
from app.services.datasets import IMPORT_ORDER, load_csv_file
from app.storage.store import Store, get_store, reset_store_for_tests

log = logging.getLogger("supplychainx.init")

DEMO_USERS = [
    ("admin@supplychainx.demo", "Aditi Admin", RoleName.ADMIN, "demo_admin_password"),
    ("manager@supplychainx.demo", "Manoj Manager", RoleName.MANAGER, "demo_manager_password"),
    ("analyst@supplychainx.demo", "Anika Analyst", RoleName.ANALYST, "demo_analyst_password"),
]


def ensure_demo_users(store: Store) -> list[str]:
    s = get_settings()
    created = []
    for email, name, role, attr in DEMO_USERS:
        pw = getattr(s, attr)
        if not pw:
            log.warning("%s not set; skipping demo user %s", attr.upper(), email)
            continue
        if user_service.get_by_email(store, email) is None:
            user_service.create(store, email, name, pw, role.value)
            created.append(email)
    return created


def archive_data_dir() -> None:
    s = get_settings()
    if s.data_dir.exists() and any(s.data_dir.iterdir()):
        dest = s.data_dir.parent / "data_archive" / f"data_{datetime.now():%Y%m%d_%H%M%S}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s.data_dir), str(dest))
        log.info("Archived previous data folder to %s", dest)


def initialise(reset: bool = False, regenerate: bool = False, train: bool = False) -> Store:
    s = get_settings()
    if reset:
        archive_data_dir()
        store = reset_store_for_tests(s.data_dir)  # fresh Store instance on the new, empty folder
    else:
        store = get_store()

    if regenerate or not (s.sample_dir / "sales.csv").exists():
        t0 = time.time()
        generate(s.sample_dir)
        log.info("Generated sample datasets in %s (%.1fs)", s.sample_dir, time.time() - t0)

    if not store.exists("sales"):
        for dataset in IMPORT_ORDER:
            t0 = time.time()
            _, n = load_csv_file(store, dataset, s.sample_dir / f"{dataset}.csv")
            log.info("Imported %-18s %7d rows (%.1fs)", dataset, n, time.time() - t0)
        log_activity(store, user_id=None, action="initialise", entity_type="dataset",
                     description="Demo data initialised from the simulated sample datasets")
    else:
        log.info("Working data already present in %s — leaving it unchanged (use --reset to start over)", s.data_dir)

    settings_service.ensure_defaults(store)
    created = ensure_demo_users(store)
    if created:
        log.info("Created demo users: %s", ", ".join(created))

    if train:
        from app.services import forecasting_service, supplier_service

        log.info("Training demand forecasting models (all products, ~3 minutes) ...")
        run = forecasting_service.train(store, user_id=None)
        log.info("Forecasting run v%s: %s", run["version"], run["status"])
        log.info("Training delivery-delay classifier ...")
        run = supplier_service.train_delay_model(store, user_id=None)
        log.info("Delay model v%s: %s", run["version"], run["status"])
    return store


if __name__ == "__main__":  # pragma: no cover
    configure_logging()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--reset", action="store_true", help="archive data/ and start from the sample datasets")
    p.add_argument("--regenerate", action="store_true", help="regenerate the sample CSVs (dates end yesterday)")
    p.add_argument("--train", action="store_true", help="train forecasting and delay models")
    p.add_argument("--train-if-untrained", action="store_true",
                   help="train only if no completed model exists yet (used by the Docker entrypoint)")
    a = p.parse_args()
    train = a.train
    if a.train_if_untrained:
        from app.services import model_registry

        st = get_store()
        train = not (model_registry.latest(st, "demand_forecast") and model_registry.latest(st, "delivery_delay"))
    initialise(reset=a.reset, regenerate=a.regenerate, train=train)
