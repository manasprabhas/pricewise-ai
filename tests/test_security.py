"""API keys never land in the database; network devices need the PIN."""
import keyring
import pytest
from keyring.backend import KeyringBackend

from pricewise import access, credentials


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


@pytest.fixture()
def memory_keyring():
    old = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    yield kr
    keyring.set_keyring(old)


def test_keys_go_to_credential_store_not_database(fresh_db, memory_keyring):
    credentials.set("gemini_api_key", "AIza-secret")
    assert credentials.get("gemini_api_key") == "AIza-secret"
    assert fresh_db.one("SELECT value FROM settings WHERE key = 'gemini_api_key'") is None
    assert "AIza-secret" not in fresh_db.db_path().read_bytes().decode("latin-1")


def test_old_keys_are_moved_out_of_the_database(fresh_db, memory_keyring):
    fresh_db.set_setting("kroger_client_secret", "old-secret")
    assert credentials.migrate_from_database() == []
    assert credentials.get("kroger_client_secret") == "old-secret"
    assert fresh_db.one("SELECT value FROM settings WHERE key = 'kroger_client_secret'") is None


def test_without_a_credential_store_keys_are_refused_not_written(fresh_db, monkeypatch):
    monkeypatch.setattr(credentials, "_keyring", lambda: None)
    with pytest.raises(credentials.CredentialStoreUnavailable):
        credentials.set("gemini_api_key", "AIza-secret")
    assert fresh_db.one("SELECT value FROM settings WHERE key = 'gemini_api_key'") is None
    monkeypatch.setenv("PRICEWISE_GEMINI_API_KEY", "from-env")
    assert credentials.get("gemini_api_key") == "from-env"


def test_pin_is_hashed_checked_and_rate_limited(fresh_db):
    with pytest.raises(ValueError):
        access.set_pin("1234")                        # too short
    access.set_pin("482913")
    stored = access._stored()
    assert stored and "482913" not in stored and fresh_db.get_setting("phone_pin") is None
    assert access.check_pin("482913", "192.168.1.50")
    for _ in range(access.MAX_TRIES):
        assert not access.check_pin("000000", "192.168.1.77")
    assert access.locked_out("192.168.1.77") > 0
    assert not access.check_pin("482913", "192.168.1.77")   # locked out even with the right PIN
    assert access.check_pin("482913", "192.168.1.50")       # other devices unaffected


def test_local_vs_network():
    assert access.is_local("127.0.0.1") and access.is_local("::1") and access.is_local("127.0.0.2")
    assert access.is_local("::ffff:127.0.0.1") and access.is_local("localhost")
    assert not access.is_local("192.168.1.50")
    # an address that is missing, empty or unreadable must never count as the laptop itself
    for unknown in (None, "", "   ", "not-an-ip", "127.example.com", "1270.0.0.1"):
        assert not access.is_local(unknown), unknown


def test_client_ip_fails_closed_when_the_peer_is_unknown(monkeypatch):
    import streamlit as st
    from streamlit.runtime import context as ctx
    monkeypatch.setattr(ctx, "_get_client_context", lambda: None)
    monkeypatch.setattr(st, "get_option", lambda k: "0.0.0.0")       # run-for-phone: listening on the network
    assert access.client_ip() is None and not access.is_local(access.client_ip())
    monkeypatch.setattr(st, "get_option", lambda k: "localhost")     # run.bat: nothing else can connect
    assert access.is_local(access.client_ip())

    class Peer:
        remote_ip = "192.168.1.50"
    monkeypatch.setattr(ctx, "_get_client_context", lambda: Peer())
    assert access.client_ip() == "192.168.1.50" and not access.is_local(access.client_ip())
