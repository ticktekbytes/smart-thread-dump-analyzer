from src.models.thread_details import ThreadDump, ThreadDetails
from src.database.db_manager import SessionLocal
import logging
import re

logger = logging.getLogger(__name__)

# ------------------- Core Tool Functions -------------------

def find_high_cpu_threads(session_id):
    logger.info(f"Calling tool: find_high_cpu_threads for session_id={session_id}")
    db = SessionLocal()
    try:
        return db.query(ThreadDump).filter_by(session_id=session_id, high_cpu_threads_exist=True).all()
    finally:
        db.close()

def find_deadlock_threads(session_id):
    logger.info(f"Calling tool: find_deadlock_threads for session_id={session_id}")
    db = SessionLocal()
    try:
        return db.query(ThreadDetails).filter_by(session_id=session_id, is_deadlocked=True).all()
    finally:
        db.close()

def find_lock_contentions(session_id):
    logger.info(f"Calling tool: find_lock_contentions for session_id={session_id}")
    db = SessionLocal()
    try:
        return db.query(ThreadDump).filter_by(session_id=session_id, lock_contentions_exist=True).all()
    finally:
        db.close()

def get_thread_state_counts(session_id: int) -> dict:
    db = SessionLocal()
    try:
        logger.info(f"Querying ThreadDetails for session_id={session_id}")
        threads = db.query(ThreadDetails).filter_by(session_id=session_id).all()
        logger.info(f"Found {len(threads)} threads for session_id={session_id}")

        state_counts = {
            'RUNNABLE': 0,
            'BLOCKED': 0,
            'WAITING': 0,
            'TIMED_WAITING': 0,
            'TOTAL': len(threads)
        }

        substate_counts = {
            'on object monitor': 0,
            'parking': 0,
            'sleeping': 0
        }

        blocked_summary = []

        for thread in threads:
            if thread.state:
                # Count main states
                if thread.state in state_counts:
                    state_counts[thread.state] += 1

                # Count sub-states
                if thread.sub_state:
                    substate_counts[thread.sub_state] = substate_counts.get(thread.sub_state, 0) + 1

                # Collect blocked thread info
                if thread.state == 'BLOCKED':
                    blocked_summary.append({
                        "name": thread.name,
                        "thread_id": thread.thread_id,
                        "sub_state": thread.sub_state,
                        "stack_trace": thread.stack_trace
                    })

        # Combine the counts
        return {
            **state_counts,
            'substates': substate_counts,
            'BLOCKED_SUMMARY': blocked_summary
        }
    finally:
        db.close()


# ------------------- Wrapper Tool Functions -------------------

def extract_session_id(input_text: str):
    input_text = input_text.strip()
    if input_text.isdigit():
        return int(input_text)
    match = re.search(r"session[ _-]?id[:=]?\s*(\d+)", input_text, re.IGNORECASE)
    return int(match.group(1)) if match else None

def find_high_cpu_threads_wrapper(input_text: str):
    session_id = extract_session_id(input_text)
    if session_id is None:
        return {"error": "Session ID not found."}
    return find_high_cpu_threads(session_id)

def find_deadlock_threads_wrapper(input_text: str):
    session_id = extract_session_id(input_text)
    if session_id is None:
        return {"error": "Session ID not found."}
    return find_deadlock_threads(session_id)

def find_lock_contentions_wrapper(input_text: str):
    session_id = extract_session_id(input_text)
    if session_id is None:
        return {"error": "Session ID not found."}
    return find_lock_contentions(session_id)

def get_thread_state_counts_wrapper(input_text: str) -> dict:
    session_id = extract_session_id(input_text)
    if session_id is None:
        return {"error": "Session ID not found."}
    return get_thread_state_counts(session_id)
