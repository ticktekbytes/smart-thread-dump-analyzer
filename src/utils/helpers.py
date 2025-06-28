import matplotlib.pyplot as plt
import io
from src.database.db_manager import SessionLocal, engine, get_db
import logging
from config import *
from src.models.thread_details import *
import re
from datetime import datetime
import random


# def example_helper_function(param1, param2):
#     """
#     This is an example helper function that performs a specific task.

#     Args:
#         param1: Description of the first parameter.
#         param2: Description of the second parameter.

#     Returns:
#         Result of the operation.
#     """
#     # Implement the logic here
#     return param1 + param2  # Example operation


def plot_thread_state_counts(counts: dict) -> io.BytesIO:
    # Prepare data
    labels = ["Runnable", "Blocked", "Waiting", "Timed Waiting"]
    values = [
        counts.get("Runnable", 0),
        counts.get("Blocked", 0),
        counts.get("Waiting", 0),
        counts.get("Timed Waiting", 0),
    ]

    # Plot
    fig, ax = plt.subplots()
    ax.bar(labels, values, color=["#4caf50", "#f44336", "#2196f3", "#ff9800"])
    ax.set_ylabel("Count")
    ax.set_title("Thread State Counts")
    plt.tight_layout()

    # Save to buffer
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close(fig)
    return buf


def create_analysis_context(thread_dumps: list) -> str:
    """Create context from thread dumps for AI analysis"""
    context = ""
    for dump in thread_dumps:
        patterns = dump["patterns"]
        context += f"\nFile: {dump['name']}\n"
        context += f"Total Threads: {len(dump['threads'])}\n"
        context += f"Blocked Threads: {len(patterns['blocked_threads'])}\n"
        context += f"High CPU Threads: {len(patterns['high_cpu_threads'])}\n"
        context += f"Lock Contentions: {len(patterns['lock_contentions'])}\n"

        if patterns["blocked_threads"]:
            context += "\nBlocked Thread Details:\n"
            for thread in patterns["blocked_threads"][:3]:
                context += f"- {thread['thread_name']}: {thread['state']}\n"

    return context


def extract_counts_from_output(output: str):
    # Match all key: value pairs, regardless of order
    pattern = r"(Runnable|Blocked|Waiting|Timed Waiting|Total Threads)\s*[:\-]\s*(\d+)"
    matches = re.findall(pattern, output)
    if matches:
        counts = {}
        for key, value in matches:
            counts[key] = int(value)
        # Ensure all keys exist for plotting
        for k in ["Total Threads", "Runnable", "Blocked", "Waiting", "Timed Waiting"]:
            counts.setdefault(k, 0)
        return counts
    return None


def reset_session_data(session_id):
    """Delete all data for a specific session and close the connection."""
    db = SessionLocal()
    try:
        # 1. Get all ThreadDetails IDs for this session
        thread_detail_ids = [
            td.id
            for td in db.query(ThreadDetails.id).filter_by(session_id=session_id).all()
        ]
        # 2. Delete ThreadLocks for those ThreadDetails
        if thread_detail_ids:
            db.query(ThreadLock).filter(
                ThreadLock.thread_id.in_(thread_detail_ids)
            ).delete(synchronize_session=False)
        # 3. Delete ThreadDetails for this session
        db.query(ThreadDetails).filter_by(session_id=session_id).delete()
        # 4. Delete ThreadDumps for this session
        db.query(ThreadDump).filter_by(session_id=session_id).delete()
        # 5. Delete the AnalysisSession itself (id, not session_id)
        db.query(AnalysisSession).filter_by(id=session_id).delete()
        db.commit()
    finally:
        db.close()


def generate_session_id():
    now = datetime.now()
    # Add a random 3-digit suffix to ensure uniqueness
    return int(now.strftime("%d%m%H%M") + f"{random.randint(100,999)}")


def summarize_stack_trace(stack_trace, max_lines=3):
    return "\n".join(stack_trace.splitlines()[:max_lines])
