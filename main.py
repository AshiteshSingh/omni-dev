import os
import asyncio
from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv
from agent import OmniDevAgent

# Load configuration from .env file
load_dotenv()

# Initialize FastAPI app
app = FastAPI(title="Omni-Dev API")

# Global agent instance
agent = None

@app.on_event("startup")
async def startup_event():
    global agent
    agent = OmniDevAgent()
    print("Omni-Dev Agent initialized.")

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str

class MemoryRequest(BaseModel):
    fact: str

@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """Chat with the Omni-Dev agent, using its long-term memory."""
    try:
        reply = await agent.chat(request.message)
        return ChatResponse(response=reply)
    except Exception as e:
        return ChatResponse(response=f"Error communicating with agent: {str(e)}")

@app.post("/api/memory/add")
async def add_memory_endpoint(request: MemoryRequest):
    """Manually add a fact to Omni-Dev's memory."""
    try:
        status = await agent.add_to_memory(request.fact)
        return {"status": status}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/memory/clear")
async def clear_memory_endpoint():
    """Clear Omni-Dev's memory (Simulate AI Amnesia)."""
    import cognee
    try:
        await cognee.prune.prune_system()
        # Ensure cognee setup is completely cleaned up
        return {"status": "Memory cleared. Amnesia induced."}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/memory")
async def get_memory_endpoint(query: str = ""):
    """Get what Omni-Dev remembers about a topic."""
    if not query:
        return {"context": "Please provide a query."}
    try:
        context = await agent.get_memory_context(query)
        return {"context": context if context else "Nothing recalled for this query."}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    # Check if credentials are set
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        print("WARNING: GOOGLE_APPLICATION_CREDENTIALS environment variable is not set.")
    
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
