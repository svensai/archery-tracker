"""
Database backup and restore utilities for the archery tracker.
Uses SQLite's built-in backup API for safe hot backups.
"""

import os
import sqlite3
import shutil
from datetime import datetime
from typing import Optional

# Resolve paths relative to this file's location
_BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATABASE_PATH = os.path.join(_BASE_DIR, 'data', 'archery.db')
BACKUP_DIR = os.path.join(_BASE_DIR, 'data', 'backups')


def create_backup(label: str = "") -> dict:
    """
    Create a safe hot backup of the SQLite database using sqlite3's backup API.
    Backups are stored in data/backups/ with timestamped filenames.

    Returns dict with path, size_bytes, and timestamp.
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    if label:
        safe_label = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in label)
        filename = f"archery_{timestamp}_{safe_label}.db"
    else:
        filename = f"archery_{timestamp}.db"

    backup_path = os.path.join(BACKUP_DIR, filename)

    # Use sqlite3's online backup API — safe while the DB is in use
    src = sqlite3.connect(DATABASE_PATH)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    size = os.path.getsize(backup_path)
    return {
        "filename": filename,
        "path": backup_path,
        "size_bytes": size,
        "timestamp": timestamp,
    }


def list_backups() -> list:
    """
    Return all backup files in data/backups/, sorted newest-first.
    Each entry has: filename, path, size_bytes, created_at.
    """
    if not os.path.isdir(BACKUP_DIR):
        return []

    entries = []
    for fname in os.listdir(BACKUP_DIR):
        if not fname.endswith('.db'):
            continue
        fpath = os.path.join(BACKUP_DIR, fname)
        stat = os.stat(fpath)
        entries.append({
            "filename": fname,
            "path": fpath,
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })

    entries.sort(key=lambda e: e["created_at"], reverse=True)
    return entries


def restore_backup(filename: str) -> dict:
    """
    Restore a named backup over the live database.
    Creates a safety backup of the current state before overwriting.

    Raises FileNotFoundError if the backup file doesn't exist.
    Returns dict with message and restored_from.
    """
    backup_path = os.path.join(BACKUP_DIR, filename)
    if not os.path.isfile(backup_path):
        raise FileNotFoundError(f"Backup not found: {filename}")

    # Safety snapshot before overwriting
    safety = create_backup(label="pre_restore")

    # Copy backup over live database using sqlite3 backup API
    src = sqlite3.connect(backup_path)
    dst = sqlite3.connect(DATABASE_PATH)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    return {
        "message": "Database restored successfully",
        "restored_from": filename,
        "safety_backup": safety["filename"],
    }
