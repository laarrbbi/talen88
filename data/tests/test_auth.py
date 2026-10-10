"""Login hardening: passwords, expiring tokens, throttling, and the production secret rule."""
from __future__ import annotations

import importlib
import time

import pytest
from fastapi.testclient import TestClient

from data import auth, db
from data.tests.conftest import _seed

PASSWORD = "correct horse battery"


@pytest.fixture()
def users(conn):
    conn.execute("INSERT INTO users (email, name, role, division, password_hash) "
                 "VALUES (?,?,?,?,?)",
                 ("admin@x.test", "Admin", "admin", None, auth.hash_password(PASSWORD)))
    conn.execute("INSERT INTO users (email, name, role, division) VALUES (?,?,?,?)",
                 ("nopass@x.test", "No Pass", "manager", "technology"))
    conn.commit()
    return conn


# ---- password hashing --------------------------------------------------------------

def test_hash_is_salted_and_verifies():
    a, b = auth.hash_password(PASSWORD), auth.hash_password(PASSWORD)
    assert a != b and PASSWORD not in a
    assert auth.verify_password(PASSWORD, a)
    assert not auth.verify_password("wrong password!", a)


def test_short_passwords_rejected():
    with pytest.raises(ValueError):
        auth.hash_password("short")


@pytest.mark.parametrize("stored", [None, "", "plain-text", "md5$abc", "scrypt$x$y$z$a$b"])
def test_malformed_hash_never_verifies(stored):
    assert not auth.verify_password(PASSWORD, stored)


# ---- authenticate ------------------------------------------------------------------------

def test_authenticate_with_correct_password(users):
    actor = auth.authenticate(users, " Admin@X.test ", PASSWORD)
    assert actor.email == "admin@x.test" and actor.is_admin


@pytest.mark.parametrize("email,password", [
    ("admin@x.test", "wrong password!"),
    ("nobody@x.test", PASSWORD),
    ("nopass@x.test", PASSWORD),      # an operator without a password cannot sign in
])
def test_authenticate_fails_generically(users, email, password):
    with pytest.raises(auth.AuthError, match="authentication failed"):
        auth.authenticate(users, email, password)


def test_set_password(users):
    auth.set_password(users, "nopass@x.test", "a brand new password")
    assert auth.authenticate(users, "nopass@x.test", "a brand new password").division == "technology"
    with pytest.raises(auth.AuthError):
        auth.set_password(users, "ghost@x.test", "a brand new password")


# ---- tokens --------------------------------------------------------------------------------

def test_token_roundtrip_and_expiry():
    actor = auth.Actor(email="a@x", name="A", role="admin", division=None)
    assert auth.decode_token(auth.issue_token(actor)) == actor
    expired = auth.issue_token(actor, ttl_seconds=-1)
    with pytest.raises(auth.AuthError, match="expired"):
        auth.decode_token(expired)


def test_tampered_token_rejected():
    actor = auth.Actor(email="m@x", name="M", role="manager", division="technology")
    body, sig = auth.issue_token(actor).split(".", 1)
    forged = auth.Actor(email="m@x", name="M", role="admin", division=None)
    forged_body = auth.issue_token(forged).split(".", 1)[0]
    with pytest.raises(auth.AuthError):
        auth.decode_token(f"{forged_body}.{sig}")


def test_token_without_expiry_rejected():
    import base64, hashlib, hmac, json  # noqa: E401
    body = base64.urlsafe_b64encode(json.dumps(
        {"email": "a@x", "name": "A", "role": "admin", "division": None}).encode()).decode()
    sig = hmac.new(auth._SECRET, body.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(auth.AuthError):
        auth.decode_token(f"{body}.{sig}")


# ---- throttle ------------------------------------------------------------------------------

def test_throttle_locks_after_max_failures_and_expires():
    t = auth.LoginThrottle(max_failures=3, window=0.2)
    for _ in range(3):
        assert not t.is_locked("A@x")
        t.record_failure("a@x")
    assert t.is_locked("a@X ")
    time.sleep(0.25)
    assert not t.is_locked("a@x")


# ---- production secret rule ----------------------------------------------------------------

@pytest.mark.parametrize("secret", ["", "change-me-set-via-fly-secrets", "too-short"])
def test_production_refuses_weak_secret(monkeypatch, secret):
    monkeypatch.setenv("PULSESCORE_ENV", "production")
    monkeypatch.setenv("PULSESCORE_SECRET", secret)
    with pytest.raises(RuntimeError):
        importlib.reload(auth)
    monkeypatch.delenv("PULSESCORE_ENV")
    monkeypatch.delenv("PULSESCORE_SECRET")
    importlib.reload(auth)


# ---- HTTP login -------------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setenv("PULSESCORE_DB", str(path))
    c = db.get_connection(path)
    db.init_db(c)
    _seed(c)
    c.execute("INSERT INTO users (email, name, role, division, password_hash) "
              "VALUES (?,?,?,?,?)",
              ("admin@x.test", "Admin", "admin", None, auth.hash_password(PASSWORD)))
    c.commit()
    c.close()
    from data import api
    api._login_throttle.reset("admin@x.test")
    with TestClient(api.app) as tc:
        yield tc


def test_login_requires_password(client):
    assert client.post("/auth/login", json={"email": "admin@x.test"}).status_code == 422
    bad = client.post("/auth/login", json={"email": "admin@x.test", "password": "nope"})
    assert bad.status_code == 401 and bad.json() == {"detail": "authentication failed"}
    ok = client.post("/auth/login", json={"email": "admin@x.test", "password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["expires_in"] > 0
    me = client.get("/me", headers={"Authorization": f"Bearer {ok.json()['token']}"})
    assert me.json()["email"] == "admin@x.test"


def test_login_locks_out_after_repeated_failures(client):
    for _ in range(5):
        assert client.post("/auth/login", json={"email": "admin@x.test",
                                                "password": "nope"}).status_code == 401
    locked = client.post("/auth/login", json={"email": "admin@x.test", "password": PASSWORD})
    assert locked.status_code == 429


def test_expired_token_is_401(client):
    actor = auth.Actor(email="admin@x.test", name="Admin", role="admin", division=None)
    token = auth.issue_token(actor, ttl_seconds=-1)
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_migrate_adds_password_column_to_old_database(tmp_path):
    c = db.get_connection(tmp_path / "old.db")
    c.execute("CREATE TABLE users (email TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT "
              "NULL, division TEXT, employee_token TEXT)")
    c.execute("INSERT INTO users VALUES ('a@x', 'A', 'admin', NULL, NULL)")
    db.migrate(c)
    db.migrate(c)   # idempotent
    cols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
    assert "password_hash" in cols
    assert c.execute("SELECT email FROM users").fetchone()[0] == "a@x"
    c.close()
