"""Test configuration.

Every test session runs against a fresh temporary data folder (never backend/data): sample datasets
are generated, imported through the validation pipeline, and a small forecasting run (3 products) plus
the delay model are trained into a temporary artifacts folder.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_tmp = Path(tempfile.mkdtemp(prefix="scx_test_"))
# Must happen before any `app` import so Settings picks them up.
os.environ["DATA_DIR"] = str(_tmp / "data")
os.environ["SAMPLE_DIR"] = str(_tmp / "sample")
os.environ["ARTIFACTS_DIR"] = str(_tmp / "artifacts")
os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)
os.environ["DEMO_ADMIN_PASSWORD"] = "Admin-test-123"
os.environ["DEMO_MANAGER_PASSWORD"] = "Manager-test-123"
os.environ["DEMO_ANALYST_PASSWORD"] = "Analyst-test-123"
os.environ["LOG_LEVEL"] = "WARNING"


@pytest.fixture(scope="session")
def seeded():
    from app.seed.init_data import initialise
    from app.services import forecasting_service, supplier_service

    store = initialise()
    run = forecasting_service.train(store, user_id=None, product_ids=[1, 2, 3])
    assert run["status"] == "completed", run.get("error")
    drun = supplier_service.train_delay_model(store, user_id=None)
    assert drun["status"] == "completed", drun.get("error")
    return store


@pytest.fixture()
def store(seeded):
    return seeded


@pytest.fixture(scope="session")
def client(seeded):
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _login(client, email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin(client):
    return _login(client, "admin@supplychainx.demo", os.environ["DEMO_ADMIN_PASSWORD"])


@pytest.fixture(scope="session")
def manager(client):
    return _login(client, "manager@supplychainx.demo", os.environ["DEMO_MANAGER_PASSWORD"])


@pytest.fixture(scope="session")
def analyst(client):
    return _login(client, "analyst@supplychainx.demo", os.environ["DEMO_ANALYST_PASSWORD"])
