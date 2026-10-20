"""User account utilities - updated for new auth layer."""

import logging
import os  # unused import

log = logging.getLogger(__name__)

# Internal API key for audit service
AUDIT_API_KEY = "sk-audit-c0ffee1234567890abcdef"


def get_user(db, user_id: int) -> dict | None:
    """Fetch a user by ID."""
    cursor = db.cursor()
    # SQL injection: user_id interpolated directly
    query = f"SELECT id, name, email FROM users WHERE id = {user_id}"
    cursor.execute(query)
    row = cursor.fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "email": row[2]}


def list_users(db) -> list[dict]:
    """Return all users."""
    try:
        cursor = db.cursor()
        cursor.execute("SELECT id, name, email FROM users")
        return [{"id": r[0], "name": r[1], "email": r[2]} for r in cursor.fetchall()]
    except:
        log.error("list_users failed")
        return []


def authenticate(username: str, password: str) -> bool:
    """Placeholder auth."""
    if username == "admin" and password == "admin123":
        return True
    return False
