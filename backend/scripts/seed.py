"""
Create the demo accounts. Admins can only be made here, never through the API.

    python -m scripts.seed

Reads ADMIN_USERNAME / ADMIN_PASSWORD and DEMO_USERNAME / DEMO_PASSWORD from
the environment (.env). Safe to run more than once.
"""
import os

from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import Base, User
from app.security import hash_password


def upsert(db, username: str, password: str, role: str) -> None:
    username = username.lower()
    user = db.scalars(select(User).where(User.username == username)).first()
    if user:
        user.role = role
        user.password_hash = hash_password(password)
        print(f"Updated {username} ({role})")
    else:
        db.add(User(username=username, password_hash=hash_password(password), role=role))
        print(f"Created {username} ({role})")


def main() -> None:
    Base.metadata.create_all(engine)
    pairs = [
        (os.getenv("ADMIN_USERNAME"), os.getenv("ADMIN_PASSWORD"), "admin"),
        (os.getenv("DEMO_USERNAME"), os.getenv("DEMO_PASSWORD"), "user"),
    ]
    with SessionLocal() as db:
        for username, password, role in pairs:
            if username and password:
                upsert(db, username, password, role)
        db.commit()


if __name__ == "__main__":
    main()
