from src.models.thread_details import Base
from src.database.db_manager import engine

Base.metadata.drop_all(engine)  # Drop old tables (for dev only!)
Base.metadata.create_all(engine)
print("Tables recreated with new columns!")