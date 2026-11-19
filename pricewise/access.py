"""Phone access over Wi-Fi is locked behind a PIN.

Opening PriceWise on the laptop itself needs no PIN. Any other device must enter it; if no PIN is set,
other devices are refused entirely. Only a salted PBKDF2 hash of the PIN is stored.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import time

from . import db

LOOPBACK_BINDS = {"localhost", "127.0.0.1", "::1"}
MAX_TRIES = 5
LOCKOUT_SECONDS = 300
_failures: dict[str, list[float]] = {}


def is_local(ip: str | None) -> bool:
    """True only for a real loopback address. A missing, empty or unreadable address is never trusted."""
    if not ip or not isinstance(ip, str):
        return False
    if ip == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(ip.strip("[]").split("%")[0])
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):          # ::ffff:127.0.0.1
        addr = addr.ipv4_mapped
    return addr.is_loopback


def _server_bound_to_loopback() -> bool:
    try:
        import streamlit as st
        return str(st.get_option("server.address") or "").strip() in LOOPBACK_BINDS
    except Exception:                                # noqa: BLE001
        return False


def client_ip() -> str | None:
    """The TCP peer of this browser session.

    st.context.ip_address deliberately turns loopback into None, which can't be told apart from "unknown". So this
    reads the raw peer address, as Streamlit's own loopback check does. If it can't be read, the session counts as
    local only when the server listens on loopback alone (run.bat), where nothing else can connect at all.
    Otherwise the answer is None, and None is never trusted.
    """
    try:
        from streamlit.runtime import context as _ctx
        cc = _ctx._get_client_context()
        ip = getattr(cc, "remote_ip", None) if cc is not None else None
    except Exception:                                # noqa: BLE001  (private API: fail closed)
        ip = None
    if ip:
        return ip
    return "127.0.0.1" if _server_bound_to_loopback() else None


def _pin_file():
    """The PIN protects the whole app, whichever data (yours or the demo) is open, so it lives in its own file."""
    import os
    base = db.db_path().parent if os.environ.get("PRICEWISE_DB") else db.DEFAULT_DB.parent
    base.mkdir(parents=True, exist_ok=True)
    return base / "phone_pin.txt"


def _stored() -> str:
    try:
        return _pin_file().read_text().strip()
    except OSError:
        return ""


def _hash(pin: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 200_000).hex()


def set_pin(pin: str) -> None:
    pin = (pin or "").strip()
    if not (pin.isdigit() and 6 <= len(pin) <= 12):
        raise ValueError("Use 6 to 12 digits.")
    salt = os.urandom(16)
    _pin_file().write_text(f"{salt.hex()}${_hash(pin, salt)}")


def clear_pin() -> None:
    try:
        _pin_file().unlink()
    except OSError:
        pass


def pin_set() -> bool:
    return bool(_stored())


def locked_out(ip: str) -> float:
    """Seconds left before this device may try again (0 = may try)."""
    recent = [t for t in _failures.get(ip, []) if time.time() - t < LOCKOUT_SECONDS]
    _failures[ip] = recent
    return LOCKOUT_SECONDS - (time.time() - recent[0]) if len(recent) >= MAX_TRIES else 0.0


def check_pin(pin: str, ip: str = "") -> bool:
    if locked_out(ip):
        return False
    stored = _stored()
    if "$" not in stored:
        return False
    salt_hex, digest = stored.split("$", 1)
    ok = hmac.compare_digest(_hash((pin or "").strip(), bytes.fromhex(salt_hex)), digest)
    if not ok:
        _failures.setdefault(ip, []).append(time.time())
    return ok
