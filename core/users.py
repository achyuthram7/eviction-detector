"""Demo staff accounts (synthetic). Passwords are listed in the README on purpose."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.constants import ADMIN, CLERK, FACILITATOR, JUDGE
from core.models import User
from core.security import hash_password

DEMO_USERS = (
    ("clerk", "Demo Clerk", CLERK, "clerk-demo"),
    ("facilitator", "Demo Facilitator", FACILITATOR, "facilitator-demo"),
    ("judge", "Demo Judge", JUDGE, "judge-demo"),
    ("admin", "Demo Admin", ADMIN, "admin-demo"),
)


def create_demo_users(session: Session) -> list[User]:
    users = []
    for username, display, role, password in DEMO_USERS:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(
                username=username,
                display_name=display,
                role=role,
                password_hash=hash_password(password),
            )
            session.add(user)
        users.append(user)
    session.flush()
    return users
