import os
import logging
from src.utils.ssl_patch import patch_ssl
from dotenv import load_dotenv

# Configure logging first
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Apply SSL patch before any other imports
try:
    cert_path = patch_ssl()
    logger.info(f"SSL configured with certificates from: {cert_path}")
except Exception as e:
    logger.error(f"SSL configuration failed: {str(e)}")
    raise

# Now import everything else
from openai import AsyncOpenAI
import chainlit as cl
from src.analyzers.thread_analyzer import ThreadDumpAnalyzer
from src.database.db_manager import SessionLocal, engine, get_db
from src.utils.helpers import create_analysis_context, generate_session_id
from config import *
from src.models.thread_details import *
from langchain.tools import Tool
from src.tools.thread_tools import *
from langchain.agents import initialize_agent, AgentType
from langchain_community.chat_models import ChatOpenAI
from sqlalchemy import text  # <-- Add this import
import asyncio
from src.utils.helpers import *
from langchain.sql_database import SQLDatabase
from langchain_experimental.sql import SQLDatabaseChain
from langchain.prompts import PromptTemplate
from langchain.agents import AgentExecutor
import uuid
from datetime import datetime

# Initialize components
client = AsyncOpenAI()
cl.instrument_openai()
analyzer = ThreadDumpAnalyzer()
session_is_reset = False
# Global variable
active_session_id = None

# Active sessions store
active_sessions = {}  # Store active sessions per browser

schema = """
thread_details (
    id INTEGER PRIMARY KEY,
    thread_dump_id INTEGER,
    session_id BIGINT,
    name VARCHAR,
    thread_id VARCHAR,
    daemon BOOLEAN,
    priority INTEGER,
    os_priority INTEGER,
    cpu_time FLOAT,
    elapsed_time FLOAT,
    tid VARCHAR,
    nid VARCHAR,
    state VARCHAR,
    sub_state VARCHAR,
    stack_trace TEXT
)
"""


settings = {
    "model": "gpt-4",
    "temperature": 0,
}

tools = [
    Tool(
        name="FindHighCPUThreads",
        func=lambda input_text: find_high_cpu_threads_wrapper(
            input_text, active_sessions.get(cl.user_session.get("browser_id"))
        ),
        description="Finds thread dumps with high CPU usage.",
    ),
    Tool(
        name="FindDeadlockThreads",
        func=lambda input_text: find_deadlock_threads_wrapper(
            input_text, active_sessions.get(cl.user_session.get("browser_id"))
        ),
        description="Finds threads that are deadlocked.",
    ),
    Tool(
        name="FindLockContentions",
        func=lambda input_text: find_lock_contentions_wrapper(
            input_text, active_sessions.get(cl.user_session.get("browser_id"))
        ),
        description="Finds thread dumps with lock contentions.",
    ),
    Tool(
        name="GetThreadStateCounts",
        func=lambda input_text: get_thread_state_counts_wrapper(
            input_text, active_sessions.get(cl.user_session.get("browser_id"))
        ),
        description="Returns thread state counts for the current browser session.",
    ),
    Tool(
        name="DynamicSQLQuery",
        func=lambda q: safe_sql_query(
            q, sql_chain, active_sessions.get(cl.user_session.get("browser_id")), schema
        ),
        description="Ask questions about threads in the current browser session.",
    ),
    Tool(
        name="GetKnowledgeBaseInsights",
        func=lambda input_text: get_knowledge_base_insights(
            input_text, active_sessions.get(cl.user_session.get("browser_id"))
        ),
        description="Get insights from knowledge base for similar threads",
    ),
]

llm = ChatOpenAI(openai_api_key=OPENAI_API_KEY, model_name="gpt-4")

# Initialize SQLDatabaseChain for dynamic NL2SQL queries
sql_db = SQLDatabase.from_uri(DATABASE_URL)

custom_sql_prompt = PromptTemplate(
    input_variables=["schema", "question"],
    template="""You are an expert SQL assistant. Use these exact rules:

Database Schema:
{schema}

IMPORTANT RULES:
1. ALWAYS use exact table name 'thread_details'
2. ALWAYS use column name 'name' for thread names
3. ALWAYS include session_id in WHERE clause
4. For thread name searches:
   - Use LIKE with % wildcards
   - Always wrap thread names in single quotes
   - Example: name LIKE '%LocalProcess%'

Example Queries:
Q: "how many LocalProcess threads?"
A: SELECT COUNT(*) FROM thread_details WHERE name LIKE '%LocalProcess%' AND session_id = <session_id>

Q: "count threads with Local in name"
A: SELECT COUNT(*) FROM thread_details WHERE name LIKE '%Local%' AND session_id = <session_id>

Your Task: Generate a SQL query for this question:
{question}
""",
)

sql_chain = SQLDatabaseChain.from_llm(
    llm=llm,
    db=sql_db,
    verbose=True,
    use_query_checker=True,
    return_intermediate_steps=True,
    return_direct=True,
    output_key="result",  # Add this line
)

custom_agent_template = """You are an expert thread dump analyzer assistant that combines real-time analysis with knowledge base insights.

Tool Selection Guide:
1. First use analysis tools:
   - GetThreadStateCounts for state overview
   - FindDeadlockThreads for lock issues
   - FindHighCPUThreads for performance issues
   - FindLockContentions for contention patterns
2. Then check knowledge base:
   - Use GetKnowledgeBaseInsights with thread IDs or patterns
   - Look for similar issues and known solutions

Analysis Strategy:
1. Analyze current thread dumps using tools
2. For each identified issue:
   - Search knowledge base for similar patterns
   - Get historical solutions and context
3. Combine findings into comprehensive response

Response Format:
1. Current Analysis:
   - Identified issues (CPU, locks, states)
   - Affected thread details
   - Pattern detection results

2. Knowledge Base Matches:
   - Similar historical issues
   - Problem descriptions
   - Known resolutions
   - Additional context from previous cases

3. Combined Recommendations:
   - Immediate actions needed
   - Long-term solutions
   - Prevention measures

Remember:
- Always check knowledge base for similar patterns
- Include problem descriptions and resolutions when found
- Link current issues to historical solutions
- Provide comprehensive remediation steps
"""

# Then update the agent initialization
agent = initialize_agent(
    tools,
    llm,
    agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
    verbose=True,
    agent_kwargs={
        "system_message": custom_agent_template,
        "format_instructions": """Use the following format:

Question: the input question you must answer
Thought: you should always think about what to do
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now know the final answer
Final Answer: the final answer to the original input question""",
    },
)


@cl.on_chat_start
async def start():
    try:
        # Generate unique browser ID if not exists
        browser_id = cl.user_session.get("browser_id")
        if not browser_id:
            browser_id = str(uuid.uuid4())
            cl.user_session.set("browser_id", browser_id)

        logger.info(f"Starting new session for browser: {browser_id}")

        # Show file upload prompt
        files = await cl.AskFileMessage(
            content="Please upload thread dump files (up to 20)",
            accept=["text/plain"],
            max_size_mb=20,
            max_files=20,
            timeout=3600,
        ).send()

        if not files:
            logger.warning("No files uploaded within timeout")
            await cl.Message(
                content="⚠️ No files were uploaded. Please refresh to try again."
            ).send()
            return

        db = SessionLocal()
        try:
            # Create new session with browser tracking
            session_id = generate_session_id()

            # Set active_session_id BEFORE creating session
            global active_session_id
            active_session_id = session_id

            session = AnalysisSession(
                id=session_id,
                client_id=cl.user_session.get("client_id") or "default",
                browser_id=browser_id,
                created_at=datetime.utcnow(),
                is_active=True,
            )

            # Deactivate any previous sessions for this browser
            db.query(AnalysisSession).filter(
                AnalysisSession.browser_id == browser_id,
                AnalysisSession.is_active == True,
            ).update({"is_active": False})

            # Save new session
            db.add(session)
            db.commit()
            db.refresh(session)

            # Update active sessions map
            active_sessions[browser_id] = session_id

            logger.info(
                f"Created new session with ID: {session_id} for browser: {browser_id}"
            )

            # Process files with validated session
            await process_uploaded_files(files, session, db)

        except Exception as e:
            logger.error(f"Database error: {str(e)}")
            db.rollback()
            active_session_id = None  # Reset on error
            await cl.Message(
                content="⚠️ Error initializing session. Please refresh the page."
            ).send()
        finally:
            db.close()

    except Exception as e:
        logger.error(f"Session startup error: {str(e)}")
        active_session_id = None  # Reset on error
        await cl.Message(
            content="⚠️ Error starting session. Please refresh the page."
        ).send()


# Add cleanup handler
@cl.on_chat_end
async def cleanup():
    browser_id = cl.user_session.get("browser_id")
    if browser_id and browser_id in active_sessions:
        del active_sessions[browser_id]
    logger.info(
        f"Chat session ended for browser {browser_id}, cleaned up session state"
    )


async def process_uploaded_files(files, session, db):
    global active_session_id

    # Ensure active_session_id matches session.id
    if active_session_id is None:
        active_session_id = session.id
    elif session.id != active_session_id:
        error_msg = f"Session ID mismatch: session.id={session.id}, active_session_id={active_session_id}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    logger.info(f"Processing files for session_id={session.id}")

    # Store session ID in local var to detect changes
    current_session_id = session.id

    for file in files:
        try:
            with open(file.path, "r") as f:
                content = f.read()
                logger.info(
                    f"Processing file {file.name} with content length: {len(content)}"
                )

                # Parse and create thread dump
                thread_dump = analyzer.parse_dump(content, file.name, session.id)
                logger.info(
                    f"Parsed thread dump with {len(thread_dump.threads) if thread_dump.threads else 0} threads"
                )

                # Ensure session ID is set
                thread_dump.session_id = session.id

                # Add and commit thread dump first
                db.add(thread_dump)
                db.flush()  # Get the ID without committing

                # Now add all thread details
                if thread_dump.threads:
                    for thread in thread_dump.threads:
                        thread.session_id = session.id
                        thread.thread_dump_id = thread_dump.id
                        db.add(thread)

                # Commit all changes
                db.commit()
                db.refresh(thread_dump)

                # Log the inserted data
                thread_count = (
                    db.query(ThreadDetails).filter_by(session_id=session.id).count()
                )
                logger.info(f"Inserted {thread_count} threads for session {session.id}")

                summary = f"""
### Thread Dump Analysis for {file.name}

#### Thread Count: {thread_count}
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
            # Log the full stack trace
            logger.exception("Full error details:")

    # Verify data was inserted
    final_thread_count = (
        db.query(ThreadDetails).filter_by(session_id=session.id).count()
    )
    logger.info(
        f"Total threads inserted for session {session.id}: {final_thread_count}"
    )

    if final_thread_count == 0:
        await cl.Message(
            content="⚠️ Warning: No thread data was inserted. Please check the thread dump format."
        ).send()
        return

    # Send success message
    await cl.Message(
        content=f"✅ Successfully processed {len(files)} files with {final_thread_count} total threads."
    ).send()

    await cl.Message(
        content="You can now ask questions about the uploaded thread dumps, such as 'get thread state counts'."
    ).send()

    await cl.Message(
        content="",
        actions=[
            cl.Action(
                name="new_session",
                payload={"action": "start_new_session"},
                label="🔄 Start New Session",
            ),
            cl.Action(
                name="refresh_kb",
                payload={"action": "refresh_knowledge_base"},
                label="🔄 Refresh Knowledge Base",
            ),
        ],
    ).send()


@cl.on_message
async def main(message: cl.Message):
    # First check if session is reset
    if session_is_reset:
        await cl.Message(
            content="❌ Session data has been cleared. Please reload the page (Cmd+R) to start a new session."
        ).send()
        return

    # Then check browser session
    browser_id = cl.user_session.get("browser_id")
    if not browser_id or browser_id not in active_sessions:
        await cl.Message(
            content="⚠️ No active session found. Please upload thread dump files first."
        ).send()
        return

    session_id = active_sessions[browser_id]
    logger.info(
        f"Processing request - Browser: {browser_id}, Session: {session_id}, Message: {message.content}"
    )

    try:
        # Log request details
        logger.info(
            f"Processing request - Session ID: {session_id}, Message: {message.content}"
        )

        # Update thinking message to show progress
        thinking_msg = cl.Message(content="🔄 Processing your request...")
        thinking_msg.content = "🤔 Analyzing thread dumps and generating insights..."
        await thinking_msg.send()

        # Use agent.invoke for sync support (no await)
        response = agent.invoke({"input": message.content})

        # Log completion
        logger.info("Agent execution complete")

        # Remove thinking message
        await thinking_msg.remove()

        # Log intermediate steps if available
        if hasattr(response, "intermediate_steps"):
            for idx, step in enumerate(response.intermediate_steps, 1):
                logger.info(f"Analysis Step {idx}: {step}")

        # Format response nicely if it's a dict with 'output'
        if isinstance(response, dict) and "output" in response:
            answer = response["output"]
        else:
            answer = str(response)

        await cl.Message(
            content=answer,
            actions=[
                cl.Action(
                    name="new_session",
                    payload={"action": "start_new_session"},
                    label="🔄 Start New Session",
                )
            ],
        ).send()

        # Try to plot if possible
        counts = None
        if isinstance(response, dict) and "thread_states" in response:
            counts = response["thread_states"]
        else:
            counts = extract_counts_from_output(answer)
        if counts:
            buf = plot_thread_state_counts(counts)
            msg = cl.Message(
                content="Here is the thread state distribution:",
                elements=[
                    cl.Image(
                        name="thread_state_counts.png",
                        display="inline",
                        content=buf.getvalue(),
                    )
                ],
            )
            await msg.send()
            # await msg.send_files([("thread_state_counts.png", buf)])

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
                    label="🔄 Start New Session",
                )
            ],
        ).send()


@cl.action_callback("new_session")
async def on_new_session(action):
    global session_is_reset, active_session_id
    db = SessionLocal()
    try:
        # Reset current session if it exists
        if active_session_id:
            reset_session_data(active_session_id)
            logger.info(f"Reset session data for ID: {active_session_id}")

        # Update global flag
        session_is_reset = True
        active_session_id = None

        # Inform user to refresh
        await cl.Message(
            content="🔄 Session has been reset. Please refresh your browser (Cmd+R on Mac, Ctrl+R on Windows) to start a new session."
        ).send()

    except Exception as e:
        logger.error(f"Error resetting session: {str(e)}")
        await cl.Message(content=f"❌ Error resetting session: {str(e)}").send()
    finally:
        db.close()


from src.rag.thread_vector_store import ThreadVectorStore


@cl.action_callback("refresh_kb")
async def on_refresh_kb(action):
    try:
        vector_store = ThreadVectorStore()
        vector_store.cleanup_vector_store()
        vector_store._create_vector_store()
        await cl.Message(
            content="✅ Knowledge base has been refreshed from the latest database entries."
        ).send()
    except Exception as e:
        logger.error(f"Error refreshing knowledge base: {str(e)}")
        await cl.Message(content=f"❌ Error refreshing knowledge base: {str(e)}").send()


# # Update the get_thread_state_counts function to use browser-specific session
# def get_thread_state_counts(input_text: str, session_id: str = None) -> dict:
#     browser_id = cl.user_session.get("browser_id")
#     if not browser_id or browser_id not in active_sessions:
#         raise ValueError("No active session found for this browser")

#     session_id = active_sessions[browser_id]
#     # ...rest of the function remains same...
