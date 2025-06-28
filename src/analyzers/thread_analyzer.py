import re
from typing import Dict, List, Tuple
from datetime import datetime
from collections import defaultdict
from src.models.thread_details import ThreadDump, ThreadDetails, ThreadLock
import logging

logger = logging.getLogger(__name__)

THREAD_HEADER_PATTERN = re.compile(
    r'"([^"]+)"\s+'                # Thread name
    r'(?:#(\d+)\s+)?'              # Optional thread number
    r'(?:daemon\s+)?'              # Optional daemon
    r'(?:prio=(\d+)\s+)?'          # Optional priority
    r'(?:os_prio=(\d+)\s+)?'       # Optional OS priority
    r'(?:cpu=([\d.]+)ms\s+)?'      # Optional CPU time
    r'(?:elapsed=([\d.]+)s\s+)?'   # Optional elapsed time
    r'.*?'                         # Any text (non-greedy)
    r'(?:tid=(0x[\da-f]+))?'       # Optional thread ID
    r'.*?'                         # Any text (non-greedy)
    r'(?:nid=(0x[\da-f]+))?'       # Optional native ID
    r'.*?'                         # Any text (non-greedy)
    r'(?:\[(.*?)\])?'              # Optional memory address
)

THREAD_STATE_PATTERN = re.compile(r'java\.lang\.Thread\.State:\s+(\w+(?:_\w+)*)')
LOCK_PATTERN = re.compile(r'- (?:waiting on|waiting to lock|locked|parking to wait for)\s+<(0x[\da-f]+)>\s+\(a ([^)]+)\)')

class ThreadDumpAnalyzer:
    def __init__(self):
        from src.rag.thread_vector_store import ThreadVectorStore
        self.rag = ThreadVectorStore()
        logger.info("Initialized ThreadDumpAnalyzer with vector store")

    def parse_dump(self, content: str, file_name: str, session_id=None) -> ThreadDump:
        """Parse thread dump content and return structured data"""
        thread_dump = ThreadDump(
            file_name=file_name,
            timestamp=datetime.utcnow(),
            session_id=session_id
        )
        
        current_thread = None
        threads = []
        stack_trace_lines = []

        for line in content.split('\n'):
            thread_match = THREAD_HEADER_PATTERN.match(line)
            if thread_match:
                # Save the previous thread if exists
                if current_thread:
                    current_thread.stack_trace = "\n".join(stack_trace_lines)
                    threads.append(current_thread)
                stack_trace_lines = []

                current_thread = ThreadDetails(
                    name=thread_match.group(1),
                    thread_id=thread_match.group(2),
                    daemon='daemon' in line,
                    priority=int(thread_match.group(3)) if thread_match.group(3) else None,
                    os_priority=int(thread_match.group(4)) if thread_match.group(4) else None,
                    cpu_time=float(thread_match.group(5)) if thread_match.group(5) else None,
                    elapsed_time=float(thread_match.group(6)) if thread_match.group(6) else None,
                    tid=thread_match.group(7),
                    nid=thread_match.group(8),
                    state=None,
                    locks=[],
                    session_id=session_id,  # <-- set this!
                )
            elif current_thread and line.strip().startswith('java.lang.Thread.State:'):
                state_line = line.split(':', 1)[1].strip()
                main_state, sub_state = self._parse_thread_state(state_line)
                current_thread.state = main_state
                current_thread.sub_state = sub_state
                stack_trace_lines.append(line)
            elif current_thread and (line.strip().startswith('- locked') or 
                                     line.strip().startswith('- waiting to lock')):
                lock_match = re.search(r'<(.+?)>', line)
                lock = ThreadLock(
                    lock_name=lock_match.group(1) if lock_match else "",
                    lock_type='MONITOR',
                    is_owner='locked' in line
                )
                current_thread.locks.append(lock)
                stack_trace_lines.append(line)
            elif current_thread:
                stack_trace_lines.append(line)

        # Add the last thread if exists
        if current_thread:
            current_thread.stack_trace = "\n".join(stack_trace_lines)
            threads.append(current_thread)
        
        thread_dump.threads = threads
        self._analyze_thread_dump(thread_dump)
        
        logger.info(f"Parsed thread dump with {len(thread_dump.threads) if thread_dump.threads else 0} threads")
        return thread_dump
    
    def _parse_thread_state(self, state_line: str) -> Tuple[str, str]:
        """Parse thread state line into main state and sub-state."""
        if not state_line:
            return None, None

        # If it's already the state value (not the full line)
        if not state_line.startswith('java.lang.Thread.State:'):
            full_state = state_line
        else:
            # Extract from full line
            state_match = THREAD_STATE_PATTERN.search(state_line)
            if not state_match:
                return None, None
            full_state = state_match.group(1)

        # Handle states with sub-states
        if ' (' in full_state:
            parts = full_state.split(' (', 1)
            main_state = parts[0].strip()
            sub_state = parts[1].rstrip(')').strip()
            return main_state, sub_state

        # No sub-state
        return full_state.strip(), None

    def _analyze_thread_dump(self, thread_dump: ThreadDump):
        """Analyze thread dump for patterns and set flags"""
        states = {
            'RUNNABLE': 0,
            'BLOCKED': 0,
            'WAITING': 0,
            'TIMED_WAITING': 0
        }
        
        substates = defaultdict(int)
        high_cpu_count = 0
        lock_contentions = 0

        for thread in thread_dump.threads:
            if not thread.state:
                continue

            # Split and store state properly
            main_state, sub_state = self._parse_thread_state(thread.state)
            if not main_state:
                continue

            # Update thread object with clean states
            thread.state = main_state
            thread.sub_state = sub_state

            # Count main states
            if main_state in states:
                states[main_state] += 1

            # Count sub-states
            if sub_state:
                substates[sub_state] += 1

            # Check for high CPU threads
            if thread.cpu_time and thread.cpu_time > 1000:
                high_cpu_count += 1
                thread_dump.high_cpu_threads_exist = True

            # Check for lock contentions
            if thread.locks and main_state == 'BLOCKED':
                lock_contentions += 1
                thread_dump.lock_contentions_exist = True

        # Update thread dump statistics
        thread_dump.total_threads = len(thread_dump.threads)
        thread_dump.running_threads = states['RUNNABLE']
        thread_dump.blocked_threads = states['BLOCKED']
        thread_dump.waiting_threads = states['WAITING']
        thread_dump.timed_waiting_threads = states['TIMED_WAITING']

        # Update sub-state counts
        thread_dump.waiting_on_object_monitor = substates.get('on object monitor', 0)
        thread_dump.timed_waiting_sleeping = substates.get('sleeping', 0)
        thread_dump.waiting_parking = substates.get('parking', 0)
        thread_dump.timed_waiting_parking = substates.get('parking', 0)

        logger.info(f"Analyzed thread dump: {thread_dump.file_name}")
        logger.info(f"States: {states}")
        logger.info(f"Substates: {substates}")
        
        return states  # Add this return

    async def analyze_with_knowledge_base(self, thread_dump: ThreadDump) -> dict:
        """Analyze thread dump using both standard analysis and knowledge base."""
        # First perform standard analysis
        states = self._analyze_thread_dump(thread_dump)
        
        kb_insights = []
        
        # Check each thread against knowledge base
        for thread in thread_dump.threads:
            try:
                # Create thread info dict for KB search
                thread_info = {
                    'name': thread.name,
                    'state': thread.state,
                    'stack_trace': thread.stack_trace,
                    'tid': thread.tid,
                    'cpu_time': thread.cpu_time
                }
                
                # Get insights from knowledge base
                similar_threads = await self.rag.find_similar_threads(thread_info)
                
                # Filter high confidence matches
                for match in similar_threads:
                    if match['similarity'] > 0.85:
                        kb_insights.append({
                            'current_thread': {
                                'name': thread.name,
                                'state': thread.state,
                                'tid': thread.tid
                            },
                            'similar_thread': match['thread'],
                            'problem_info': match.get('problem_info', {}),
                            'similarity': match['similarity']
                        })
                        
            except Exception as e:
                logger.error(f"Error getting KB insights for thread {thread.name}: {str(e)}")
                
        return {
            'standard_analysis': {
                'states': states,
                'high_cpu_exists': thread_dump.high_cpu_threads_exist,
                'lock_contentions_exist': thread_dump.lock_contentions_exist,
                'total_threads': thread_dump.total_threads
            },
            'kb_insights': kb_insights
        }
