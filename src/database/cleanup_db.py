from src.database.db_manager import SessionLocal
from src.models.thread_details import AnalysisSession, ThreadDump, ThreadDetails, ThreadLock

def cleanup_tables():
    db = SessionLocal()
    try:
        db.query(ThreadLock).delete()
        db.query(ThreadDetails).delete()
        db.query(ThreadDump).delete()
        db.query(AnalysisSession).delete()
        db.commit()
        print("All tables cleaned!")
    finally:
        db.close()

if __name__ == "__main__":
    cleanup_tables()