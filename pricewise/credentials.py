"""API keys and secrets live in the operating system's credential store, never in the database.

Windows: Credential Manager. macOS: Keychain. Linux: Secret Service. (Via the free `keyring` library.)
An environment variable PRICEWISE_<NAME> overrides the store, e.g. PRICEWISE_GEMINI_API_KEY.
If no credential store is available, secrets can only come from environment variables: PriceWise refuses to
write them into its database or any file.
"""
from __future__ import annotations

import os

from . import db

SERVICE = "PriceWise"
NAMES = ("gemini_api_key", "kroger_client_id", "kroger_client_secret", "usda_api_key")


class CredentialStoreUnavailable(RuntimeError):
    pass


def _keyring():
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return None
    kr = keyring.get_keyring()
    if isinstance(kr, fail.Keyring) or "fail" in type(kr).__module__ or "null" in type(kr).__module__:
        return None
    return keyring


def available() -> bool:
    return _keyring() is not None


def get(name: str) -> str:
    env = os.environ.get(f"PRICEWISE_{name.upper()}")
    if env:
        return env.strip()
    kr = _keyring()
    if kr is None:
        return ""
    try:
        return (kr.get_password(SERVICE, name) or "").strip()
    except Exception:
        return ""


def set(name: str, value: str) -> None:  # noqa: A001 - mirrors keyring's API
    kr = _keyring()
    if kr is None:
        raise CredentialStoreUnavailable(
            "No secure credential store found on this computer, so the key was not saved. Set the environment "
            f"variable PRICEWISE_{name.upper()} instead.")
    value = (value or "").strip()
    if value:
        kr.set_password(SERVICE, name, value)
    else:
        try:
            kr.delete_password(SERVICE, name)
        except Exception:
            pass


def migrate_from_database() -> list[str]:
    """Move any keys an older version stored in the settings table into the credential store, then erase them
    from the database. Keys stay in the database only if there is nowhere safe to put them (reported)."""
    moved, stuck = [], []
    for name in NAMES:
        row = db.one("SELECT value FROM settings WHERE key = ?", (name,))
        if not row or not (row["value"] or "").strip():
            db.run("DELETE FROM settings WHERE key = ?", (name,))
            continue
        try:
            set(name, row["value"])
            db.run("DELETE FROM settings WHERE key = ?", (name,))
            moved.append(name)
        except CredentialStoreUnavailable:
            stuck.append(name)
    return stuck
