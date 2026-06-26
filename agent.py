import os
import vertexai
from vertexai.generative_models import GenerativeModel
import cognee

class OmniDevAgent:
    def __init__(self):
        self.project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        # Initialize Vertex AI
        if self.project_id:
            vertexai.init(project=self.project_id, location=self.location)
        self.model = GenerativeModel("gemini-1.5-pro")
        
        # We also ensure cognee knows our preferences
        cognee.config.set_llm_config({
            "llm_provider": "google_vertex_ai",
            "llm_model": "gemini-1.5-pro"
        })

    async def add_to_memory(self, text: str):
        """Store context into Cognee to prevent AI Amnesia."""
        await cognee.add(text, dataset_name="user_memory")
        await cognee.cognify()
        return "Context saved to long-term memory."

    async def get_memory_context(self, query: str):
        """Retrieve relevant context from Cognee based on the user query."""
        try:
            results = await cognee.search("SEARCH_TYPE_INSIGHTS", query_text=query)
            if results:
                # Concatenate insights to inject into the prompt
                return "\n".join(str(res) for res in results)
            return ""
        except Exception as e:
            print(f"Error recalling memory: {e}")
            return ""

    async def chat(self, prompt: str):
        """The main interaction loop with Gemini and Cognee memory."""
        # 1. Fetch relevant long-term memory context
        memory_context = await self.get_memory_context(prompt)
        
        # 2. Construct the prompt with injected memory context
        system_instructions = "You are Omni-Dev, a highly capable autonomous coding agent. " \
                              "You have a long-term memory graph that stores user preferences, architecture, and past workflows. "
        
        full_prompt = system_instructions + "\n\n"
        if memory_context:
            full_prompt += f"--- RECALLED LONG-TERM MEMORY CONTEXT ---\n{memory_context}\n------------------------------------------\n\n"
        
        full_prompt += f"User: {prompt}\nAgent:"
        
        # 3. Generate response
        response = self.model.generate_content(full_prompt)
        agent_reply = response.text
        
        # 4. Automatically store the interaction in long-term memory for future context
        # In a real app we might summarize this before storing to save graph space.
        interaction_summary = f"User asked: '{prompt}'. Omni-Dev resolved or answered this context."
        await self.add_to_memory(interaction_summary)
        
        return agent_reply
