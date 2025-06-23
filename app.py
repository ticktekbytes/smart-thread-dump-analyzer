from openai import AsyncOpenAI
import chainlit as cl
from src.analyzers.thread_analyzer import ThreadDumpAnalyzer
from src.database.db_manager import SessionLocal, engine, get_db
from src.utils.helpers import create_analysis_context
from config import *
from src.models.thread_details import AnalysisSession, ThreadDump, ThreadDetails, ThreadLock
from langchain.tools import Tool
from src.tools.thread_tools import *
from langchain.agents import initialize_agent, AgentType
from langchain_openai import ChatOpenAI  # Updated import
from sqlalchemy import text 



client = AsyncOpenAI()

# Instrument the OpenAI client
cl.instrument_openai()
analyzer = ThreadDumpAnalyzer()

settings = {
    "model": "gpt-4",
    "temperature": 0,
}


tools = [
    Tool(
        name="FindHighCPUThreads",
        func=find_high_cpu_threads_wrapper,
        description="Finds thread dumps with high CPU usage."
    ),
    Tool(
        name="FindDeadlockThreads",
        func=find_deadlock_threads_wrapper,
        description="Finds threads that are deadlocked."
    ),
    Tool(
        name="FindLockContentions",
        func=find_lock_contentions_wrapper,
        description="Finds thread dumps with lock contentions."
    ),
    Tool(
        name="GetThreadStateCounts",
        func=get_thread_state_counts_wrapper,
        description="Returns a dictionary with counts of each thread state (RUNNABLE, BLOCKED, WAITING, TIMED_WAITING, and substates) for the given session."
    ),
    # Tool(
    #     name="GetSpecificThreadStateCount",
    #     func=get_specific_thread_state_count_wrapper,
    #     description="Returns the count for a specific thread state (e.g., RUNNABLE, WAITING, TIMED_WAITING, BLOCKED, or substates) for the given session."
    # ),
]

llm = ChatOpenAI(
    api_key=OPENAI_API_KEY, 
    model_name="gpt-4",
    temperature=0
)
agent = initialize_agent(
    tools,
    llm,
    agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
    verbose=True
)

@cl.on_chat_start
async def start():
    # Start a new analysis session
    db = SessionLocal()
    try:
        session = AnalysisSession()
        db.add(session)
        db.commit()
        db.refresh(session)

        files = None
        while files is None:
            files = await cl.AskFileMessage(
                content="Please upload thread dump files (up to 20)",
                accept=["text/plain"],
                max_size_mb=20,
                max_files=20,
            ).send()

        for file in files:
            try:
                with open(file.path, 'r') as f:
                    content = f.read()
                    # Pass session.id to the parser!
                    thread_dump = analyzer.parse_dump(content, file.name, session.id)
                    thread_dump.session_id = session.id
                    db.add(thread_dump)
                    db.commit()
                    db.refresh(thread_dump)

                    summary = f"""
### Thread Dump Analysis for {file.name}

#### Detected Patterns:
- Blocked Threads: {thread_dump.blocked_threads}
- High CPU Threads: {thread_dump.high_cpu_threads_exist}
- Lock Contentions: {thread_dump.lock_contentions_exist}
"""
                    await cl.Message(content=summary).send()
            except Exception as e:
                db.rollback()
                error_msg = f"Error processing {file.name}: {str(e)}"
                logger.error(error_msg)
                await cl.Message(content=f"❌ {error_msg}").send()
    finally:
        db.close()

@cl.on_message
async def main(message: cl.Message):
    try:
        # Get the latest analysis session
        db = SessionLocal()
        try:
            session = db.query(AnalysisSession).order_by(AnalysisSession.created_at.desc()).first()
            if not session:
                await cl.Message(
                    content="⚠️ No analysis session found. Please upload thread dump files first."
                ).send()
                return
            session_id = session.id
        finally:
            db.close()

        # Log request details
        logger.info(f"Processing request - Session ID: {session_id}, Message: {message.content}")

        # Update thinking message to show progress
        thinking_msg = cl.Message(content="🔄 Processing your request...")
        thinking_msg.content = "🤔 Analyzing thread dumps and generating insights..."
        await thinking_msg.send()

        # Run the agent
        response = agent.run({
            "input": f"{message.content}\nSession ID: {session_id}"
        })

        # Log completion
        logger.info("Agent execution complete")

        # Remove thinking message
        await thinking_msg.remove()

        # Log intermediate steps if available
        if hasattr(response, "intermediate_steps"):
            for idx, step in enumerate(response.intermediate_steps, 1):
                logger.info(f"Analysis Step {idx}: {step}")
        
        # Send response with action button
        await cl.Message(
            content=str(response),
            actions=[
                cl.Action(
                    name="new_session",
                    payload={"action": "start_new_session"},
                    label="🔄 Start New Session"
                )
            ]
        ).send()

    except Exception as e:
        logger.error(f"Error in message handler: {str(e)}")
        
        # Check if it's a context length error
        error_message = "❌ An error occurred while processing your request.\n"
        if "context length" in str(e).lower():
            error_message += (
                "The thread dump analysis is too large for processing.\n"
                "Please start a new session with fewer or smaller thread dumps."
            )
        else:
            error_message += "Please try again or start a new session."

        # Remove the thinking message
        await thinking_msg.remove()
        
        # Send error message with the action button
        await cl.Message(
            content=error_message,
            actions=[
                cl.Action(
                    name="new_session",
                    payload={"action": "start_new_session"},
                    label="🔄 Start New Session"
                )
            ]
        ).send()

@cl.action_callback("new_session")
async def on_new_session(action):
    # Get the latest session
    db = SessionLocal()
    try:
        session = db.query(AnalysisSession).order_by(AnalysisSession.created_at.desc()).first()
        if session:
            reset_session_data(session.id)
            await cl.Message(content="✅ Current session data cleared. Please upload new thread dump files to start a fresh session.").send()
        else:
            await cl.Message(content="No session found to reset.").send()
    finally:
        db.close()

def reset_session_data(session_id):
    """Delete all data for a specific session and close the connection."""
    db = SessionLocal()
    try:
        # 1. Get all ThreadDetails IDs for this session
        thread_detail_ids = [
            td.id for td in db.query(ThreadDetails.id).filter_by(session_id=session_id).all()
        ]
        # 2. Delete ThreadLocks for those ThreadDetails
        if thread_detail_ids:
            db.query(ThreadLock).filter(ThreadLock.thread_id.in_(thread_detail_ids)).delete(synchronize_session=False)
        # 3. Delete ThreadDetails for this session
        db.query(ThreadDetails).filter_by(session_id=session_id).delete()
        # 4. Delete ThreadDumps for this session
        db.query(ThreadDump).filter_by(session_id=session_id).delete()
        # 5. Delete the AnalysisSession itself (id, not session_id)
        db.query(AnalysisSession).filter_by(id=session_id).delete()
        db.commit()
    finally:
        db.close()