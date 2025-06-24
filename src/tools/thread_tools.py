from src.models.thread_details import ThreadDump, ThreadDetails, ThreadLock  # Import all models from thread_details
from src.database.db_manager import SessionLocal
import logging
import re
from collections import defaultdict

logger = logging.getLogger(__name__)

# ------------------- Core Tool Functions -------------------

def find_high_cpu_threads(session_id):
    logger.info(f"Calling tool: find_high_cpu_threads for session_id={session_id}")
    db = SessionLocal()
    try:
        # Find threads with high CPU usage
        high_cpu_threads = (
            db.query(ThreadDetails)
            .filter(
                ThreadDetails.session_id == session_id,
                ThreadDetails.state == 'RUNNABLE'
            )
            .order_by(ThreadDetails.cpu_time.desc())
            .limit(10)  # Get top 10 CPU consuming threads
            .all()
        )
        
        logger.info(f"Found {len(high_cpu_threads)} high CPU threads")
        
        # Format the response
        result = []
        for thread in high_cpu_threads:
            thread_info = {
                "thread_id": thread.thread_id,
                "thread_name": thread.name,
                "state": thread.state,
                "cpu_time": thread.cpu_time,
                "stack_trace": thread.stack_trace
            }
            
            # Add additional context if available
            if hasattr(thread, 'elapsed_time'):
                thread_info["elapsed_time"] = thread.elapsed_time
            if hasattr(thread, 'priority'):
                thread_info["priority"] = thread.priority
                
            result.append(thread_info)
        
        # Sort by CPU time
        result.sort(key=lambda x: x.get("cpu_time", 0), reverse=True)
        
        logger.info(f"Analyzed CPU usage for {len(result)} threads")
        return result
        
    except Exception as e:
        logger.error(f"Error in find_high_cpu_threads: {str(e)}")
        return {"error": f"Failed to analyze high CPU threads: {str(e)}"}
    finally:
        db.close()

def find_deadlock_threads(session_id):
    logger.info(f"Calling tool: find_deadlock_threads for session_id={session_id}")
    db = SessionLocal()
    try:
        # Find threads that are in BLOCKED state and have associated locks
        deadlocked_threads = (
            db.query(ThreadDetails)
            .join(ThreadLock)
            .filter(
                ThreadDetails.session_id == session_id,
                ThreadDetails.state == 'BLOCKED'
            )
            .all()
        )
        
        logger.info(f"Found {len(deadlocked_threads)} potentially deadlocked threads")
        
        # Format the response
        result = []
        for thread in deadlocked_threads:
            result.append({
                "thread_id": thread.thread_id,
                "thread_name": thread.name,
                "state": thread.state,
                "sub_state": thread.sub_state,
                "locks": [
                    {
                        "lock_name": lock.lock_name,
                        "lock_type": lock.lock_type,
                        "is_owner": lock.is_owner
                    }
                    for lock in thread.locks
                ]
            })
        
        return result
        
    except Exception as e:
        logger.error(f"Error in find_deadlock_threads: {str(e)}")
        return {"error": f"Failed to analyze deadlocks: {str(e)}"}
    finally:
        db.close()

def find_lock_contentions(session_id):
    logger.info(f"Calling tool: find_lock_contentions for session_id={session_id}")
    db = SessionLocal()
    try:
        # Find threads that are either BLOCKED or WAITING and have associated locks
        contended_threads = (
            db.query(ThreadDetails)
            .join(ThreadLock)
            .filter(
                ThreadDetails.session_id == session_id,
                ThreadDetails.state.in_(['BLOCKED', 'WAITING']),
                ThreadLock.is_owner == False  # Looking for threads waiting on locks
            )
            .all()
        )
        
        logger.info(f"Found {len(contended_threads)} threads with lock contentions")
        
        # Group contentions by lock
        lock_contentions = {}
        for thread in contended_threads:
            for lock in thread.locks:
                if lock.lock_name not in lock_contentions:
                    lock_contentions[lock.lock_name] = {
                        "lock_type": lock.lock_type,
                        "waiting_threads": [],
                        "owner_thread": None
                    }
                
                if not lock.is_owner:
                    lock_contentions[lock.lock_name]["waiting_threads"].append({
                        "thread_id": thread.thread_id,
                        "thread_name": thread.name,
                        "state": thread.state,
                        "sub_state": thread.sub_state,
                        "stack_trace": thread.stack_trace
                    })
                else:
                    lock_contentions[lock.lock_name]["owner_thread"] = {
                        "thread_id": thread.thread_id,
                        "thread_name": thread.name
                    }
        
        # Format the final response
        result = []
        for lock_name, contention in lock_contentions.items():
            result.append({
                "lock_name": lock_name,
                "lock_type": contention["lock_type"],
                "owner_thread": contention["owner_thread"],
                "waiting_threads": contention["waiting_threads"],
                "contention_count": len(contention["waiting_threads"])
            })
        
        # Sort by contention count
        result.sort(key=lambda x: x["contention_count"], reverse=True)
        
        logger.info(f"Analyzed {len(result)} lock contentions")
        return result
        
    except Exception as e:
        logger.error(f"Error in find_lock_contentions: {str(e)}")
        return {"error": f"Failed to analyze lock contentions: {str(e)}"}
    finally:
        db.close()

def get_thread_state_counts(session_id: int) -> dict:
    db = SessionLocal()
    try:
        logger.info(f"Querying ThreadDetails for session_id={session_id}")
        threads = db.query(ThreadDetails).filter_by(session_id=session_id).all()
        logger.info(f"Found {len(threads)} threads for session_id={session_id}")

        # Initialize counts
        state_counts = {
            'RUNNABLE': 0,
            'BLOCKED': 0,
            'WAITING': 0,
            'TIMED_WAITING': 0,
            'TOTAL': len(threads)
        }

        substate_counts = defaultdict(int)
        blocked_count = 0

        for thread in threads:
            if thread.state:
                # Count main states
                if thread.state in state_counts:
                    state_counts[thread.state] += 1

                # Count sub-states without storing details
                if thread.sub_state:
                    substate_counts[thread.sub_state] += 1

                # Just count blocked threads
                if thread.state == 'BLOCKED':
                    blocked_count += 1

        # Format the response with just the counts
        return {
            'thread_states': {
                'Total Threads': state_counts['TOTAL'],
                'Runnable': state_counts['RUNNABLE'],
                'Blocked': state_counts['BLOCKED'],
                'Waiting': state_counts['WAITING'],
                'Timed Waiting': state_counts['TIMED_WAITING']
            },
            'substate_summary': dict(substate_counts),
            'blocked_thread_count': blocked_count
        }

    except Exception as e:
        logger.error(f"Error getting thread state counts: {str(e)}")
        return {"error": f"Failed to get thread state counts: {str(e)}"}
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
