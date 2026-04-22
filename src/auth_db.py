"""
Database functions for users and favourite archer groups.
"""
import sqlite3
import os
import bcrypt
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

# Import the shared DB path
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from src.database import DATABASE_PATH, get_connection


def create_auth_tables() -> None:
    """Create users, groups, group_archers tables if they don't exist."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_verified BOOLEAN DEFAULT 0,
            is_admin BOOLEAN DEFAULT 0,
            verification_token TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, name)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS group_archers (
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            archer_id INTEGER NOT NULL REFERENCES archers(id) ON DELETE CASCADE,
            PRIMARY KEY (group_id, archer_id)
        )
    ''')

    # Schema migrations — add columns that didn't exist in earlier versions
    _migrations = [
        'ALTER TABLE users ADD COLUMN verification_token_expires_at TIMESTAMP',
        'ALTER TABLE users ADD COLUMN password_reset_token TEXT',
        'ALTER TABLE users ADD COLUMN password_reset_expires_at TIMESTAMP',
    ]
    for sql in _migrations:
        try:
            cur.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists

    conn.commit()
    conn.close()


def ensure_admin_user() -> None:
    """Create the default admin user if it doesn't exist."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE email = 'admin@archery.local'")
    if cur.fetchone() is None:
        pw_hash = bcrypt.hashpw(b'Admin123!', bcrypt.gensalt()).decode()
        cur.execute(
            "INSERT INTO users (email, username, password_hash, is_verified, is_admin) VALUES (?,?,?,1,1)",
            ('admin@archery.local', 'admin', pw_hash)
        )
        conn.commit()
        print("Default admin created: admin@archery.local / Admin123!")
    conn.close()


def create_user(email: str, username: str, password_hash: str, verification_token: Optional[str]) -> int:
    conn = get_connection()
    cur = conn.cursor()
    expires_at = (datetime.utcnow() + timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S') if verification_token else None
    cur.execute(
        """INSERT INTO users
           (email, username, password_hash, verification_token, verification_token_expires_at)
           VALUES (?,?,?,?,?)""",
        (email, username, password_hash, verification_token, expires_at)
    )
    user_id = cur.lastrowid
    conn.commit()
    conn.close()
    return user_id


def get_user_by_id(user_id: int) -> Optional[Dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[Dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = ?", (email,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_username(username: str) -> Optional[Dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def verify_user_token(token: str) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT id FROM users
           WHERE verification_token = ?
             AND is_verified = 0
             AND (verification_token_expires_at IS NULL
                  OR verification_token_expires_at > datetime('now'))""",
        (token,)
    )
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE users SET is_verified=1, verification_token=NULL, verification_token_expires_at=NULL WHERE id=?",
            (row['id'],)
        )
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False


def set_verification_token(user_id: int, token: str) -> None:
    expires_at = (datetime.utcnow() + timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET verification_token=?, verification_token_expires_at=? WHERE id=?",
        (token, expires_at, user_id)
    )
    conn.commit()
    conn.close()


def delete_user(user_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_all_users() -> List[Dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, email, username, is_verified, is_admin, created_at FROM users ORDER BY created_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ── Groups ──────────────────────────────────────────────────────────────────

def create_group(user_id: int, name: str) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO groups (user_id, name) VALUES (?,?)", (user_id, name))
    group_id = cur.lastrowid
    conn.commit()
    conn.close()
    return group_id


def get_user_groups(user_id: int) -> List[Dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT g.id, g.name, g.created_at,
               COUNT(ga.archer_id) AS archer_count
        FROM groups g
        LEFT JOIN group_archers ga ON ga.group_id = g.id
        WHERE g.user_id = ?
        GROUP BY g.id
        ORDER BY g.name
    ''', (user_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_group(group_id: int) -> Optional[Dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM groups WHERE id = ?", (group_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_group(group_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM groups WHERE id = ?", (group_id,))
    conn.commit()
    conn.close()


def add_archer_to_group(group_id: int, archer_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO group_archers (group_id, archer_id) VALUES (?,?)", (group_id, archer_id))
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # already in group
    conn.close()


def remove_archer_from_group(group_id: int, archer_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM group_archers WHERE group_id = ? AND archer_id = ?", (group_id, archer_id))
    conn.commit()
    conn.close()


def get_group_archers(group_id: int) -> List[Dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT a.* FROM archers a
        JOIN group_archers ga ON ga.archer_id = a.id
        WHERE ga.group_id = ?
        ORDER BY a.name
    ''', (group_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ── Password reset ───────────────────────────────────────────────────────────

def set_password_reset_token(user_id: int, token: str) -> None:
    """Store a password-reset token valid for 1 hour."""
    expires_at = (datetime.utcnow() + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET password_reset_token=?, password_reset_expires_at=? WHERE id=?",
        (token, expires_at, user_id)
    )
    conn.commit()
    conn.close()


def get_user_by_reset_token(token: str) -> Optional[Dict]:
    """Return user if the reset token exists and has not expired."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT * FROM users
           WHERE password_reset_token = ?
             AND password_reset_expires_at > datetime('now')""",
        (token,)
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_user_password(user_id: int, password_hash: str) -> None:
    """Update password hash and clear the reset token."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET password_hash=?, password_reset_token=NULL, password_reset_expires_at=NULL WHERE id=?",
        (password_hash, user_id)
    )
    conn.commit()
    conn.close()
