import keyring
import pytest
from keyring.backend import KeyringBackend

from app.core import db


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def get_password(self, service, username):
        return self.store.get((service, username))

    def delete_password(self, service, username):
        from keyring.errors import PasswordDeleteError

        if (service, username) not in self.store:
            raise PasswordDeleteError("not found")
        del self.store[(service, username)]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated ~/Reachout replacement."""
    monkeypatch.setenv("REACHOUT_HOME", str(tmp_path / "Reachout"))
    db.reset_engines()
    yield tmp_path / "Reachout"
    db.reset_engines()


@pytest.fixture
def engine(home):
    from app.core import config

    config.ensure_dirs()
    eng = db.get_engine()
    db.migrate(eng)
    return eng


@pytest.fixture
def memory_keyring():
    old = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(old)
