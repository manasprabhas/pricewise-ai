import os
import tempfile
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# never touch your real data/pricewise.db while testing
os.environ["PRICEWISE_DB"] = str(Path(tempfile.mkdtemp()) / "collect.db")


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICEWISE_DB", str(tmp_path / "test.db"))
    from pricewise import db
    db.init_db()
    return db


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "demo.db"
    os.environ["PRICEWISE_DB"] = str(path)
    from pricewise import demo
    demo.load_demo()
    yield path


@pytest.fixture()
def demo(demo_db, monkeypatch):
    monkeypatch.setenv("PRICEWISE_DB", str(demo_db))
    from pricewise import db
    return db
