import ipaddress
import shutil
import socket

import keyring
import pytest
from keyring.backend import KeyringBackend

from backend.core import config, db


# ---------------------------------------------------------------- no network
# Unit tests never reach the internet, Gmail, DNS or a real SMTP/IMAP server. Loopback stays open (local SMTP sink,
# test servers). A test that genuinely needs the outside world carries @pytest.mark.live.

def _is_local(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode()
    if host in (None, "", "localhost", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_ip(host) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        return
    real_connect, real_connect_ex, real_sendto, real_gai = (
        socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto, socket.getaddrinfo)

    def _block(addr) -> None:
        if isinstance(addr, tuple) and addr and not _is_local(addr[0]):
            raise RuntimeError(f"network access in a unit test: {addr[0]!r} (use a fake, or mark the test @pytest.mark.live)")

    monkeypatch.setattr(socket.socket, "connect", lambda s, a: (_block(a), real_connect(s, a))[1])
    monkeypatch.setattr(socket.socket, "connect_ex", lambda s, a: (_block(a), real_connect_ex(s, a))[1])
    monkeypatch.setattr(socket.socket, "sendto", lambda s, d, *a: (_block(a[-1]), real_sendto(s, d, *a))[1])

    def _gai(host, *a, **k):
        if not _is_local(host) and not _is_ip(host):  # an IP literal needs no DNS; connect() still blocks it
            raise RuntimeError(f"DNS lookup in a unit test: {host!r} (use a fake, or mark the test @pytest.mark.live)")
        return real_gai(host, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", _gai)


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


@pytest.fixture(scope="session")
def _template_db(tmp_path_factory):
    """Migrate once per session; each test gets a file copy instead of re-running every migration."""
    path = tmp_path_factory.mktemp("template") / "reachout.db"
    eng = db.get_engine(path)
    db.migrate(eng)
    with eng.begin() as c:
        c.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    db.reset_engines()
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated ~/Reachout replacement."""
    monkeypatch.setenv("REACHOUT_HOME", str(tmp_path / "Reachout"))
    db.reset_engines()
    yield tmp_path / "Reachout"
    db.reset_engines()


@pytest.fixture
def engine(home, _template_db):
    config.ensure_dirs()
    shutil.copyfile(_template_db, config.db_path())
    eng = db.get_engine()
    db.migrate(eng)  # no-op unless the template is older than the code
    return eng


@pytest.fixture
def memory_keyring():
    old = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(old)
