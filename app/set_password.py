"""Set sign-in passwords. Run inside the API container:

    docker exec -it uyamaa_fleet_api python -m app.set_password someone@example.com 'NewPassword1'
    docker exec -it uyamaa_fleet_api python -m app.set_password --all 'TempPassword1'

Creates the user_credential table if it does not exist yet.
"""
import sys

from sqlalchemy import func

from . import auth, models
from .database import SessionLocal, engine


def main(argv):
    if len(argv) != 3 or len(argv[2]) < 8:
        print(__doc__)
        print("The password must be at least 8 characters.")
        return 1

    models.UserCredential.__table__.create(bind=engine, checkfirst=True)
    target, password = argv[1], argv[2]
    db = SessionLocal()
    try:
        if target == "--all":
            users = db.query(models.User).order_by(models.User.user_id).all()
        else:
            users = db.query(models.User).filter(func.lower(models.User.email) == target.strip().lower()).all()
        if not users:
            print(f"No user found for {target}.")
            return 1
        for u in users:
            cred = db.get(models.UserCredential, u.user_id)
            hashed = auth.hash_password(password)
            if cred is None:
                db.add(models.UserCredential(user_id=u.user_id, password_hash=hashed))
            else:
                cred.password_hash = hashed
            print(f"Password set for {u.full_name} <{u.email}>")
        db.commit()
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
