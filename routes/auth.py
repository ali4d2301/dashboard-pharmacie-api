import time
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from db import get_db
from security import verify_password, create_access_token

router = APIRouter(prefix="/api/auth", tags=["auth"])

class LoginIn(BaseModel):
    username: str
    password: str


@router.get("/ready")
def auth_ready(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.post("/login")
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.execute(
    text("""
        SELECT id, username, password_hash, role, is_active
        FROM users
        WHERE username = :u
    """),
    {"u": data.username}
    ).mappings().first()

    if not user or not user["is_active"]:
        time.sleep(0.6)
        raise HTTPException(status_code=401, detail="Identifiants invalides")

    if not verify_password(data.password, user["password_hash"]):
        time.sleep(0.6)
        raise HTTPException(status_code=401, detail="Identifiants invalides")

    token = create_access_token({
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
    })
    return {"access_token": token, "token_type": "bearer", "role": user["role"]}
