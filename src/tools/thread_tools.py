from src.models.thread_details import (
    ThreadDump,
    ThreadDetails,
    ThreadLock,
)  # Import all models from thread_details
from src.database.db_manager import SessionLocal
import logging
import re
from collections import defaultdict
import inspect
from langchain_experimental.sql import SQLDatabaseChain
from src.models.knowledge_base import ThreadKnowledge, ProblemInfo
from src.utils.helpers import summarize_stack_trace
from sqlalchemy import select
from src.rag.thread_vector_store import ThreadVectorStore
import asyncio

logger = logging.getLogger(__name__)

# Initialize vector store
MAX_STACK_TRACE_CHARS = 200  # Limit stack trace length for performance
MAX_THREADS_PER_LOCK = 5
vector_store = ThreadVectorStore()

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
                ThreadDetails.state == "RUNNABLE",
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
                # "stack_trace": thread.stack_trace
            }

            # Add additional context if available
            if hasattr(thread, "elapsed_time"):
                thread_info["elapsed_time"] = thread.elapsed_time
            if hasattr(thread, "priority"):
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
                ThreadDetails.session_id == session_id, ThreadDetails.state == "BLOCKED"
            )
            .all()
        )

        logger.info(f"Found {len(deadlocked_threads)} potentially deadlocked threads")

        # Format the response
        result = []
        for thread in deadlocked_threads:
            result.append(
                {
                    "thread_id": thread.thread_id,
                    "thread_name": thread.name,
                    "state": thread.state,
                    "sub_state": thread.sub_state,
                    "locks": [
                        {
                            "lock_name": lock.lock_name,
                            "lock_type": lock.lock_type,
                            "is_owner": lock.is_owner,
                        }
                        for lock in thread.locks
                    ],
                }
            )

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
                ThreadDetails.state.in_(["BLOCKED", "WAITING"]),
                ThreadLock.is_owner == False,  # Looking for threads waiting on locks
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
                        "owner_thread": None,
                    }

                if not lock.is_owner:
                    lock_contentions[lock.lock_name]["waiting_threads"].append(
                        {
                            "thread_id": thread.thread_id,
                            "thread_name": thread.name,
                            "state": thread.state,
                            "sub_state": thread.sub_state,
                            "stack_trace": summarize_stack_trace(thread.stack_trace),
                        }
                    )
                else:
                    lock_contentions[lock.lock_name]["owner_thread"] = {
                        "thread_id": thread.thread_id,
                        "thread_name": thread.name,
                    }

        # Format the final response
        result = []
        for lock_name, contention in lock_contentions.items():
            waiting_threads = contention["waiting_threads"][:MAX_THREADS_PER_LOCK]
            omitted_count = len(contention["waiting_threads"]) - MAX_THREADS_PER_LOCK
            entry = {
                "lock_name": lock_name,
                "lock_type": contention["lock_type"],
                "owner_thread": contention["owner_thread"],
                "waiting_threads": waiting_threads,
                "contention_count": len(contention["waiting_threads"]),
            }
            if omitted_count > 0:
                entry["note"] = (
                    f"{omitted_count} additional waiting threads omitted for brevity."
                )
            result.append(entry)

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
        # Only fetch state and sub_state columns
        threads = (
            db.query(ThreadDetails.state, ThreadDetails.sub_state)
            .filter_by(session_id=session_id)
            .all()
        )
        logger.info(f"Found {len(threads)} threads for session_id={session_id}")

        # Initialize counts
        state_counts = {
            "RUNNABLE": 0,
            "BLOCKED": 0,
            "WAITING": 0,
            "TIMED_WAITING": 0,
            "TOTAL": len(threads),
        }

        substate_counts = defaultdict(int)
        blocked_count = 0

        for state, sub_state in threads:
            if state:
                if state in state_counts:
                    state_counts[state] += 1
                if sub_state:
                    substate_counts[sub_state] += 1
                if state == "BLOCKED":
                    blocked_count += 1

        return {
            "thread_states": {
                "Total Threads": state_counts["TOTAL"],
                "Runnable": state_counts["RUNNABLE"],
                "Blocked": state_counts["BLOCKED"],
                "Waiting": state_counts["WAITING"],
                "Timed Waiting": state_counts["TIMED_WAITING"],
            },
            "substate_summary": dict(substate_counts),
            "blocked_thread_count": blocked_count,
        }

    except Exception as e:
        logger.error(f"Error getting thread state counts: {str(e)}")
        return {"error": f"Failed to get thread state counts: {str(e)}"}
    finally:
        db.close()


# ------------------- Wrapper Tool Functions -------------------

# def extract_session_id(input_text: str):
#     logger.info(f"Tool called: {inspect.currentframe().f_code.co_name}")
#     input_text = input_text.strip()
#     if input_text.isdigit():
#         return int(input_text)
#     match = re.search(r"session[ _-]?id[:=]?\s*(\d+)", input_text, re.IGNORECASE)
#     return int(match.group(1)) if match else None


def find_high_cpu_threads_wrapper(input_text: str, session_id):
    if not session_id:
        raise ValueError("No active session found for this browser")
    logger.info(f"Tool called: {inspect.currentframe().f_code.co_name}")
    if session_id is None:
        return {"error": "Session ID not found."}
    return find_high_cpu_threads(session_id)


def find_deadlock_threads_wrapper(input_text: str, session_id):
    if not session_id:
        raise ValueError("No active session found for this browser")
    logger.info(f"Tool called: {inspect.currentframe().f_code.co_name}")
    if session_id is None:
        return {"error": "Session ID not found."}
    return find_deadlock_threads(session_id)


def find_lock_contentions_wrapper(input_text: str, session_id):
    if not session_id:
        raise ValueError("No active session found for this browser")
    logger.info(f"Tool called: {inspect.currentframe().f_code.co_name}")
    if session_id is None:
        return {"error": "Session ID not found."}
    return find_lock_contentions(session_id)


def get_thread_state_counts_wrapper(input_text: str, session_id: str) -> dict:
    """Wrapper for getting thread state counts."""
    if not session_id:
        raise ValueError("No active session found for this browser")

    logger.info(
        f"Tool called: get_thread_state_counts_wrapper for session_id={session_id}"
    )
    result = get_thread_state_counts(session_id)
    # Format a user-friendly string
    thread_states = result.get("thread_states", {})
    summary = (
        f"Thread state counts for the session:\n"
        f"- Total Threads: {thread_states.get('Total Threads', 0)}\n"
        f"- Runnable: {thread_states.get('Runnable', 0)}\n"
        f"- Blocked: {thread_states.get('Blocked', 0)}\n"
        f"- Waiting: {thread_states.get('Waiting', 0)}\n"
        f"- Timed Waiting: {thread_states.get('Timed Waiting', 0)}"
    )
    return {"output": summary, "thread_states": thread_states}


def safe_sql_query(
    search_term: str, sql_chain: SQLDatabaseChain, session_id: str, schema: str
) -> dict:
    if not session_id:
        raise ValueError("No active session found for this browser")
    """Execute a safe SQL query with proper input parameters."""
    logger.info(f"Calling tool: DynamicSQLQuery for session_id={session_id}")

    try:
        # Build schema context
        schema_context = """
        Table thread_details contains:
        - session_id (text)
        - name (text) - thread name
        - state (text) - thread state
        """

        # Build the natural language question
        question = f"Count threads from thread_details table where name contains '{search_term}' and session_id is {session_id}"
        logger.info(f"Processing question: {question}")

        # Execute query through SQLDatabaseChain with all required parameters
        chain_response = sql_chain.invoke(
            {
                "schema": schema_context,
                "question": question,
                "query": question,  # Add this line
            }
        )

        logger.info(f"Chain response: {chain_response}")

        # Handle the response
        if isinstance(chain_response, dict) and "result" in chain_response:
            count = chain_response["result"]
            return {
                "result": count,
                "answer": f"Found {count} threads with names containing '{search_term}'",
            }
        else:
            logger.warning(f"Unexpected response format: {chain_response}")
            return {
                "error": "Invalid response format",
                "answer": "Received an unexpected response format from the database.",
            }

    except Exception as e:
        error_msg = f"Error executing query: {str(e)}"
        logger.error(f"SQL Query error: {error_msg}")
        return {
            "error": error_msg,
            "answer": "I encountered an error while searching for threads. Please try rephrasing your question.",
        }


def get_knowledge_base_insights(input_text: str, session_id: str = None) -> dict:
    """Get insights from knowledge base for thread patterns in current session."""
    try:
        # Validate session
        if not session_id:
            return {
                "error": "No active session found",
                "message": "Please upload thread dumps first",
            }

        db = SessionLocal()
        try:
            # Get threads from current session
            threads = (
                db.query(ThreadDetails)
                .filter(
                    ThreadDetails.session_id == session_id,
                    ThreadDetails.name.ilike(f"%{input_text}%"),
                )
                .all()
            )

            if not threads:
                return {
                    "message": f"No threads found matching '{input_text}' in current session"
                }

            results = {"similar_patterns": [], "known_problems": [], "resolutions": []}

            # Check each thread against knowledge base
            for thread in threads:
                thread_info = {
                    "name": thread.name,
                    "state": thread.state,
                    "stack_trace": summarize_stack_trace(thread.stack_trace),
                    "tid": thread.tid,
                }

                # Vector similarity search
                vector_matches = asyncio.run(
                    vector_store.find_similar_threads(thread_info)
                )

                # Get thread IDs from vector matches
                thread_tids = [
                    match["thread"]["tid"]
                    for match in vector_matches
                    if match["similarity"] > 0.85
                ]

                # Query problem_info for these threads
                if thread_tids:
                    stmt = select(ProblemInfo).where(
                        ProblemInfo.thread_tid.in_(thread_tids)
                    )
                    problems = db.execute(stmt).scalars().all()

                    for problem in problems:
                        results["known_problems"].append(
                            {
                                "thread_name": thread.name,
                                "thread_tid": problem.thread_tid,
                                "problem_type": problem.problem_type,
                                "description": problem.problem_description,
                                "resolution": problem.resolution,
                                "defect_info": problem.defect_info,
                                "other_info": problem.other_info,
                            }
                        )

            # Format response
            if results["known_problems"]:
                return {
                    "success": True,
                    "message": f"Found {len(results['known_problems'])} similar issues",
                    "results": results,
                }
            else:
                return {
                    "success": True,
                    "message": "No similar issues found in knowledge base",
                    "results": None,
                }

        finally:
            db.close()

    except Exception as e:
        logger.error(f"Error getting knowledge base insights: {str(e)}")
        return {"error": str(e), "message": "Error searching knowledge base"}
