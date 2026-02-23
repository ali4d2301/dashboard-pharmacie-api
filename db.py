from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from settings import settings

# Engine SQLAlchemy (MySQL Aiven)
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=280,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
