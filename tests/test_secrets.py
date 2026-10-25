import os

from app.core import config, db, secrets
from app.core.models import AppSettings


def test_roundtrip_and_delete(memory_keyring):
    secrets.set_app_password("Me@Gmail.com", "abcd efgh ijkl mnop")
    assert secrets.get_app_password("me@gmail.com") == "abcdefghijklmnop"
    assert secrets.has_app_password("ME@gmail.com")
    assert secrets.delete_app_password("me@gmail.com")
    assert not secrets.has_app_password("me@gmail.com")
    assert not secrets.delete_app_password("me@gmail.com")


def test_blank_email_has_no_password(memory_keyring):
    assert secrets.get_app_password("") is None


def test_password_never_written_to_disk(engine, memory_keyring):
    pw = "zzqqxxwwvvuuttss"
    secrets.set_app_password("me@gmail.com", pw)
    db.save_settings(AppSettings(gmail_address="me@gmail.com"), engine)
    for root, _dirs, files in os.walk(config.home()):
        for f in files:
            data = open(os.path.join(root, f), "rb").read()
            assert pw.encode() not in data, f"password leaked into {f}"
