"""
Flask-Login User class and email helper.
"""
import secrets
from flask_login import UserMixin


class User(UserMixin):
    def __init__(self, user_dict):
        self.id = user_dict['id']
        self.email = user_dict['email']
        self.username = user_dict['username']
        self.is_admin = bool(user_dict.get('is_admin', False))
        self.is_verified = bool(user_dict.get('is_verified', False))


def generate_verification_token() -> str:
    """Generate a secure random URL-safe token."""
    return secrets.token_urlsafe(32)


def send_password_reset_email(app, mail, email: str, token: str) -> None:
    """Send password-reset email. Falls back to console when MAIL_SERVER is not set."""
    base_url = app.config.get('BASE_URL', 'http://localhost:5001')
    reset_url = f"{base_url}/?reset={token}"

    if not app.config.get('MAIL_SERVER'):
        print(f"\n[DEV] Password-reset link for {email}:\n  {reset_url}\n")
        return

    from flask_mail import Message
    msg = Message(
        subject="Tilbakestill passord — Archery Tracker",
        recipients=[email],
        body=f"Klikk lenken for å tilbakestille passordet ditt (gyldig 1 time):\n\n{reset_url}\n",
        html=f'<p>Klikk lenken for å tilbakestille passordet ditt (gyldig 1 time):</p>'
             f'<p><a href="{reset_url}">{reset_url}</a></p>',
    )
    mail.send(msg)


def send_verification_email(app, mail, email: str, token: str) -> None:
    """
    Send verification email. Falls back to console logging if MAIL_SERVER is not set.
    """
    base_url = app.config.get('BASE_URL', 'http://localhost:5001')
    verify_url = f"{base_url}/api/auth/verify/{token}"

    if not app.config.get('MAIL_SERVER'):
        print(f"\n[DEV] Verification link for {email}:\n  {verify_url}\n")
        return

    from flask_mail import Message
    msg = Message(
        subject="Bekreft din konto — Archery Tracker",
        recipients=[email],
        body=f"Klikk lenken for å bekrefte din e-post:\n\n{verify_url}\n\nLenken utløper ikke.\n",
        html=f'<p>Klikk lenken for å bekrefte din e-post:</p><p><a href="{verify_url}">{verify_url}</a></p>',
    )
    mail.send(msg)
