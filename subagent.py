import sys
import asyncio
import warnings
from dotenv import load_dotenv

# Suppress warnings
warnings.filterwarnings("ignore", category=UserWarning)

from agent import OmniDevAgent

# Load environment variables
load_dotenv()

async def run_subagent(task_description: str, subagent_id: str):
    """
    Runs a headless instance of the OmniDevAgent to solve a specific task,
    and forces it to record its output to Cognee memory.
    """
    try:
        agent = OmniDevAgent()
        
        # We append an explicit instruction to the prompt to force the agent to use `remember`
        full_prompt = (
            f"SUBAGENT INSTRUCTION: You are sub-agent '{subagent_id}'. Your task is: '{task_description}'.\n"
            "You MUST operate autonomously. Once you have finished your task, you MUST use the `remember` tool "
            "to write a final report of what you did and your findings into Cognee memory so the main agent can read it."
        )
        
        # Execute the task headlessly
        await agent.execute_task(full_prompt)
        
    except Exception as e:
        # If the sub-agent fails critically, we try to log the failure to memory
        try:
            error_agent = OmniDevAgent()
            await error_agent._tool_remember(f"Sub-agent '{subagent_id}' failed with error: {str(e)}")
        except:
            pass

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python subagent.py <task_description> <subagent_id>")
        sys.exit(1)
        
    task_desc = sys.argv[1]
    s_id = sys.argv[2]
    
    asyncio.run(run_subagent(task_desc, s_id))
