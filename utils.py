import html
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database.models import UserModel


def safe_html(text: str | None) -> str:
    """Escapes string for Telegram HTML parse mode safely."""
    if text is None:
        return ""
    return html.escape(str(text))


def get_user_display_name(user: "UserModel") -> str:
    """Returns safe Telegram HTML representation for a user."""
    if user.username:
        return f"@{html.escape(user.username)}"
    
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    if not full_name:
        full_name = f"Студент {user.id}"
    
    return f'<a href="tg://user?id={user.id}">{html.escape(full_name)}</a>'


def get_user_mention_raw(user_id: int, username: str | None, first_name: str | None = None, last_name: str | None = None) -> str:
    """Returns safe Telegram HTML representation from raw user info."""
    if username:
        return f"@{html.escape(username)}"
    
    full_name = f"{first_name or ''} {last_name or ''}".strip()
    if not full_name:
        full_name = f"Студент {user_id}"
    
    return f'<a href="tg://user?id={user_id}">{html.escape(full_name)}</a>'
