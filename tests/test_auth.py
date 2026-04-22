"""
Tests for authentication, groups, and admin endpoints.
"""
import sqlite3
import pytest
import bcrypt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def auth_app(monkeypatch, tmp_path):
    """Flask app + test client wired to a temp DB, with real auth (no LOGIN_DISABLED)."""
    db_file = str(tmp_path / 'test_auth.db')
    monkeypatch.setattr('src.database.DATABASE_PATH', db_file)

    from src.database import init_database
    init_database()

    from src.auth_db import create_auth_tables
    create_auth_tables()

    from src.app import app
    app.config['TESTING'] = True
    app.config['LOGIN_DISABLED'] = False
    app.config['WTF_CSRF_ENABLED'] = False
    # Disable rate limiting in tests
    app.config['RATELIMIT_ENABLED'] = False

    with app.test_client() as client:
        yield client, db_file


def _create_verified_user(db_file, email='user@test.com', username='testuser',
                           password='Test1234!', is_admin=False):
    """Helper: insert a verified user directly into the temp DB."""
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (email, username, password_hash, is_verified, is_admin) VALUES (?,?,?,1,?)",
        (email, username, pw_hash, 1 if is_admin else 0)
    )
    user_id = cur.lastrowid
    conn.commit()
    conn.close()
    return user_id


def _login(client, email='user@test.com', password='Test1234!'):
    return client.post('/api/auth/login', json={'email': email, 'password': password})


# ── /api/auth/me ──────────────────────────────────────────────────────────────

class TestAuthMe:
    def test_me_unauthenticated(self, auth_app):
        client, _ = auth_app
        resp = client.get('/api/auth/me')
        assert resp.status_code == 200
        assert resp.json['authenticated'] is False

    def test_me_authenticated(self, auth_app):
        client, db_file = auth_app
        _create_verified_user(db_file)
        _login(client)
        resp = client.get('/api/auth/me')
        assert resp.json['authenticated'] is True
        assert resp.json['user']['username'] == 'testuser'


# ── /api/auth/register ────────────────────────────────────────────────────────

class TestRegister:
    def test_register_success(self, auth_app):
        client, _ = auth_app
        resp = client.post('/api/auth/register', json={
            'email': 'new@test.com', 'username': 'newuser', 'password': 'Secret99!'
        })
        assert resp.status_code == 201
        assert 'user_id' in resp.json

    def test_register_duplicate_email(self, auth_app):
        client, db_file = auth_app
        _create_verified_user(db_file)
        resp = client.post('/api/auth/register', json={
            'email': 'user@test.com', 'username': 'other', 'password': 'Secret99!'
        })
        assert resp.status_code == 409

    def test_register_duplicate_username(self, auth_app):
        client, db_file = auth_app
        _create_verified_user(db_file)
        resp = client.post('/api/auth/register', json={
            'email': 'other@test.com', 'username': 'testuser', 'password': 'Secret99!'
        })
        assert resp.status_code == 409

    def test_register_short_password(self, auth_app):
        client, _ = auth_app
        resp = client.post('/api/auth/register', json={
            'email': 'x@test.com', 'username': 'xuser', 'password': '1234567'
        })
        assert resp.status_code == 400

    def test_register_short_username(self, auth_app):
        client, _ = auth_app
        resp = client.post('/api/auth/register', json={
            'email': 'x@test.com', 'username': 'ab', 'password': 'Secret99!'
        })
        assert resp.status_code == 400

    def test_register_invalid_email(self, auth_app):
        client, _ = auth_app
        resp = client.post('/api/auth/register', json={
            'email': 'notanemail', 'username': 'validuser', 'password': 'Secret99!'
        })
        assert resp.status_code == 400


# ── /api/auth/login ───────────────────────────────────────────────────────────

class TestLogin:
    def test_login_success(self, auth_app):
        client, db_file = auth_app
        _create_verified_user(db_file)
        resp = _login(client)
        assert resp.status_code == 200
        assert resp.json['user']['username'] == 'testuser'

    def test_login_wrong_password(self, auth_app):
        client, db_file = auth_app
        _create_verified_user(db_file)
        resp = client.post('/api/auth/login', json={'email': 'user@test.com', 'password': 'wrong'})
        assert resp.status_code == 401

    def test_login_unverified(self, auth_app):
        client, db_file = auth_app
        # Create unverified user
        pw_hash = bcrypt.hashpw(b'Test1234!', bcrypt.gensalt()).decode()
        conn = sqlite3.connect(db_file)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute(
            "INSERT INTO users (email, username, password_hash, is_verified) VALUES (?,?,?,0)",
            ('unverified@test.com', 'unverified', pw_hash)
        )
        conn.commit(); conn.close()
        resp = client.post('/api/auth/login', json={'email': 'unverified@test.com', 'password': 'Test1234!'})
        assert resp.status_code == 403

    def test_login_unknown_email(self, auth_app):
        client, _ = auth_app
        resp = client.post('/api/auth/login', json={'email': 'nobody@test.com', 'password': 'Test1234!'})
        assert resp.status_code == 401


# ── /api/auth/forgot-password + reset-password ───────────────────────────────

class TestPasswordReset:
    def test_forgot_always_returns_200(self, auth_app):
        client, _ = auth_app
        resp = client.post('/api/auth/forgot-password', json={'email': 'nobody@test.com'})
        assert resp.status_code == 200

    def test_reset_with_valid_token(self, auth_app):
        client, db_file = auth_app
        _create_verified_user(db_file)
        # Trigger forgot-password to set a token
        client.post('/api/auth/forgot-password', json={'email': 'user@test.com'})
        # Read token directly from DB
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        row = conn.execute("SELECT password_reset_token FROM users WHERE email='user@test.com'").fetchone()
        conn.close()
        token = row['password_reset_token']
        assert token is not None
        # Reset password
        resp = client.post('/api/auth/reset-password', json={'token': token, 'password': 'NewPass99!'})
        assert resp.status_code == 200
        # Login with new password
        resp2 = client.post('/api/auth/login', json={'email': 'user@test.com', 'password': 'NewPass99!'})
        assert resp2.status_code == 200

    def test_reset_with_invalid_token(self, auth_app):
        client, _ = auth_app
        resp = client.post('/api/auth/reset-password', json={'token': 'bogus', 'password': 'NewPass99!'})
        assert resp.status_code == 400

    def test_reset_short_password(self, auth_app):
        client, _ = auth_app
        resp = client.post('/api/auth/reset-password', json={'token': 'anything', 'password': 'short'})
        assert resp.status_code == 400


# ── /api/groups ───────────────────────────────────────────────────────────────

class TestGroups:
    def test_get_groups_empty(self, auth_app):
        client, db_file = auth_app
        _create_verified_user(db_file)
        _login(client)
        resp = client.get('/api/groups')
        assert resp.status_code == 200
        assert resp.json == []

    def test_create_group(self, auth_app):
        client, db_file = auth_app
        _create_verified_user(db_file)
        _login(client)
        resp = client.post('/api/groups', json={'name': 'My Team'})
        assert resp.status_code == 201
        assert resp.json['name'] == 'My Team'

    def test_create_group_missing_name(self, auth_app):
        client, db_file = auth_app
        _create_verified_user(db_file)
        _login(client)
        resp = client.post('/api/groups', json={'name': ''})
        assert resp.status_code == 400

    def test_create_duplicate_group(self, auth_app):
        client, db_file = auth_app
        _create_verified_user(db_file)
        _login(client)
        client.post('/api/groups', json={'name': 'Team A'})
        resp = client.post('/api/groups', json={'name': 'Team A'})
        assert resp.status_code == 409

    def test_delete_group(self, auth_app):
        client, db_file = auth_app
        _create_verified_user(db_file)
        _login(client)
        create_resp = client.post('/api/groups', json={'name': 'To Delete'})
        group_id = create_resp.json['id']
        del_resp = client.delete(f'/api/groups/{group_id}')
        assert del_resp.status_code == 200
        groups = client.get('/api/groups').json
        assert all(g['id'] != group_id for g in groups)

    def test_groups_isolated_between_users(self, auth_app):
        client, db_file = auth_app
        _create_verified_user(db_file, email='u1@test.com', username='user1')
        _create_verified_user(db_file, email='u2@test.com', username='user2')

        _login(client, email='u1@test.com')
        client.post('/api/groups', json={'name': 'Private'})

        # Log out and log in as user2
        client.post('/api/auth/logout')
        _login(client, email='u2@test.com')
        groups = client.get('/api/groups').json
        assert groups == []


# ── /api/admin ────────────────────────────────────────────────────────────────

class TestAdmin:
    def test_list_users_as_admin(self, auth_app):
        client, db_file = auth_app
        _create_verified_user(db_file, is_admin=True)
        _login(client)
        resp = client.get('/api/admin/users')
        assert resp.status_code == 200
        assert any(u['username'] == 'testuser' for u in resp.json)

    def test_list_users_non_admin_forbidden(self, auth_app):
        client, db_file = auth_app
        _create_verified_user(db_file, is_admin=False)
        _login(client)
        resp = client.get('/api/admin/users')
        assert resp.status_code == 403

    def test_delete_user_as_admin(self, auth_app):
        client, db_file = auth_app
        admin_id = _create_verified_user(db_file, email='admin@test.com', username='admin', is_admin=True)
        target_id = _create_verified_user(db_file, email='target@test.com', username='target')
        _login(client, email='admin@test.com')
        resp = client.delete(f'/api/admin/users/{target_id}')
        assert resp.status_code == 200
        users = client.get('/api/admin/users').json
        assert all(u['id'] != target_id for u in users)

    def test_admin_cannot_delete_self(self, auth_app):
        client, db_file = auth_app
        admin_id = _create_verified_user(db_file, is_admin=True)
        _login(client)
        resp = client.delete(f'/api/admin/users/{admin_id}')
        assert resp.status_code == 400
