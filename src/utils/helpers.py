def example_helper_function(param1, param2):
    """
    This is an example helper function that performs a specific task.
    
    Args:
        param1: Description of the first parameter.
        param2: Description of the second parameter.
    
    Returns:
        Result of the operation.
    """
    # Implement the logic here
    return param1 + param2  # Example operation

# Additional utility functions can be added below as needed.

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
        
        if patterns['blocked_threads']:
            context += "\nBlocked Thread Details:\n"
            for thread in patterns['blocked_threads'][:3]:
                context += f"- {thread['thread_name']}: {thread['state']}\n"
    
    return context