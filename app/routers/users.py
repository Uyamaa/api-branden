from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db

router = APIRouter(prefix="/api", tags=["users"])


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    """Read-only directory. Insert point 9."""
    rows = db.query(models.User).order_by(models.User.user_id).all()
    return {"items": [{"id": u.user_id, "name": u.full_name, "email": u.email, "role": u.role} for u in rows]}
