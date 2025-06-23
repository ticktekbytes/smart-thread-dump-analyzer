from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config import DATABASE_URL

# Create the SQLAlchemy engine using the DATABASE_URL from config.py
engine = create_engine(
    DATABASE_URL,
    pool_size=10,         # default is 5
    max_overflow=20,      # default is 10
    pool_timeout=30,      # seconds
)

# Create a configured "Session" class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """Dependency to get a SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()