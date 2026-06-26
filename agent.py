import os
import subprocess
import asyncio
import vertexai
from vertexai.generative_models import (
    FunctionDeclaration,
    GenerativeModel,
    Part,
    Tool,
)
import cognee

# Define Function Declarations for the Agent
read_file_func = FunctionDeclaration(
    name="read_file",
    description="Read the contents of a local file.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative path to the file."}
        },
        "required": ["path"]
    }
)

write_file_func = FunctionDeclaration(
    name="write_file",
    description="Write or overwrite content to a local file.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative path to the file."},
            "content": {"type": "string", "description": "The full text content to write."}
        },
        "required": ["path", "content"]
    }
)

run_command_func = FunctionDeclaration(
    name="run_command",
    description="Execute a shell command (e.g., npm run build, ls, pytest).",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run."}
        },
        "required": ["command"]
    }
)

remember_func = FunctionDeclaration(
    name="remember",
    description="Store a fact, user preference, or project context into long-term Cognee graph memory.",
    parameters={
        "type": "object",
        "properties": {
            "fact": {"type": "string", "description": "The fact or context to remember."}
        },
        "required": ["fact"]
    }
)

recall_func = FunctionDeclaration(
    name="recall",
    description="Search long-term Cognee graph memory for past context or facts.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What you want to search for."}
        },
        "required": ["query"]
    }
)

omni_tools = Tool(
    function_declarations=[
        read_file_func,
        write_file_func,
        run_command_func,
        remember_func,
        recall_func
    ]
)

class OmniDevAgent:
    def __init__(self):
        self.project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        if self.project_id:
            vertexai.init(project=self.project_id, location=self.location)
        
        self.model = GenerativeModel(
            "gemini-1.5-pro",
            tools=[omni_tools],
            system_instruction="You are Omni-Dev, a highly capable autonomous coding agent. You have access to tools to read files, write files, run terminal commands, and use Cognee for long-term memory. Use tools to solve the user's task."
        )
        self.chat_session = self.model.start_chat()
        
        cognee.config.set_llm_config({
            "llm_provider": "google_vertex_ai",
            "llm_model": "gemini-1.5-pro"
        })

    # --- Tool Implementations ---
    def _tool_read_file(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {e}"

    def _tool_write_file(self, path: str, content: str) -> str:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully wrote to {path}"
        except Exception as e:
            return f"Error writing file: {e}"

    def _tool_run_command(self, command: str) -> str:
        try:
            # We run the command and capture output
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
            output = result.stdout + "\n" + result.stderr
            return output if output.strip() else "Command executed successfully with no output."
        except subprocess.TimeoutExpired:
            return "Command timed out."
        except Exception as e:
            return f"Error running command: {e}"

    async def _tool_remember(self, fact: str) -> str:
        try:
            await cognee.add(fact, dataset_name="user_memory")
            await cognee.cognify()
            return "Fact successfully saved to long-term memory."
        except Exception as e:
            return f"Error saving memory: {e}"

    async def _tool_recall(self, query: str) -> str:
        try:
            results = await cognee.search("SEARCH_TYPE_INSIGHTS", query_text=query)
            if results:
                return "\n".join(str(res) for res in results)
            return "No relevant memories found."
        except Exception as e:
            return f"Error recalling memory: {e}"

    async def execute_task(self, prompt: str, progress_callback=None):
        """
        Sends the prompt to Gemini and automatically handles tool calls in a loop
        until Gemini returns a final text response.
        """
        response = self.chat_session.send_message(prompt)
        
        while True:
            # Check if Gemini wants to call a tool
            if response.function_calls:
                function_responses = []
                for function_call in response.function_calls:
                    func_name = function_call.name
                    args = {key: val for key, val in function_call.args.items()}
                    
                    if progress_callback:
                        progress_callback(func_name, args)

                    # Execute the corresponding tool
                    if func_name == "read_file":
                        result = self._tool_read_file(**args)
                    elif func_name == "write_file":
                        result = self._tool_write_file(**args)
                    elif func_name == "run_command":
                        result = self._tool_run_command(**args)
                    elif func_name == "remember":
                        result = await self._tool_remember(**args)
                    elif func_name == "recall":
                        result = await self._tool_recall(**args)
                    else:
                        result = f"Unknown tool: {func_name}"

                    # Package the result to send back to Gemini
                    function_responses.append(
                        Part.from_function_response(
                            name=func_name,
                            response={"content": result}
                        )
                    )
                
                # Send tool results back to the model
                response = self.chat_session.send_message(function_responses)
            else:
                # No more function calls, we have a final text response
                break
                
        return response.text
