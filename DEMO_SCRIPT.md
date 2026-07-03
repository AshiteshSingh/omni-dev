# Omni-Dev 3-Minute Demo Script

This script focuses on a high-impact, side-by-side live comparison to immediately prove the value of Omni-Dev's persistent memory.

> [!IMPORTANT]
> **Pre-recording Checklist**
> - Have two terminals ready to go side-by-side.
> - Ensure you are authenticated with Claude Code on the left, and Omni-Dev on the right.
> - Have your `.env` file configured with a working LLM key for Omni-Dev.
> - Increase terminal font sizes so the text is easily readable on video.

---

## Act 1: The Hook & The Problem (0:00 - 0:20)
**What to show:** Your face or a title card, then transition to your code editor showing `src/tools/memory_tools.py`.

**What to say:**
> "Every AI coding assistant today suffers from the exact same problem: amnesia. 
> For the Hangover AI hackathon, I built **Omni-Dev**: a coding agent that runs on *any* model and cures AI amnesia using a permanent, queryable memory graph via Cognee. Let me show you what that actually means in practice."

---

## Act 2: The Side-by-Side Test (0:20 - 1:15)
**What to show:** Open two terminal windows side-by-side. 
Left Terminal: Run `claude` (Claude Code).
Right Terminal: Run `omni` (Omni-Dev).

**What to say:**
> "On the left, we have Claude Code, one of the best assistants on the market. On the right, we have Omni-Dev. Let's teach them both a simple fact."

**Action:** 
In BOTH terminals, type:
`> Remember that the secret deploy key rotates every 19 days, and the project codename is ZORBLAX-9.`
Wait for both to acknowledge it.

**What to say:**
> "They both got it. Now, let's simulate the 'AI Hangover'."

**Action:** 
Kill BOTH sessions (press `Ctrl+C` or type `exit`). Clear the terminals. Start them both up again from scratch (`claude` on the left, `omni` on the right).

**What to say:**
> "We've started fresh sessions. Let's ask them what they remember."

**Action:** 
In BOTH terminals, type:
`> What is the project codename, and how often does the key rotate?`

**What to say:**
> *(Point to Claude Code)* "Claude Code has forgotten everything. It's completely amnesiac. 
> *(Point to Omni-Dev)* But Omni-Dev answers instantly: ZORBLAX-9, every 19 days. It didn't read this from a context file; it recalled it dynamically from its persistent Cognee knowledge graph."

---

## Act 3: Outliving the Code (1:15 - 2:00)
**What to show:** Maximize the Omni-Dev terminal. Clear it. Run `python proof_repo_memory.py`.

**What to say:**
> "It gets better. Most agents rely on scanning your local files for context. Omni-Dev stores knowledge independently of your files."

**Action:** Let the script finish and highlight the final output showing `PASS`.

**What to say:**
> "This script creates a dummy file with a secret code, feeds it into Omni-Dev's memory, and then completely DELETES the repository. Even with the source files permanently gone from the hard drive, the agent still recalls the code perfectly. The knowledge outlives the files."

---

## Act 4: Model Agnosticism (2:00 - 2:40)
**What to show:** Inside the `omni` CLI, type `/model` and switch the provider (e.g., from Gemini to Claude, or to a local Ollama model).

**What to say:**
> "And because this is built on LiteLLM, we aren't locked to one vendor. I can switch models on the fly—from Gemini to Claude, or even a completely offline local Ollama model—and the memory engine automatically follows. Same agent, same permanent memory, different brain."

---

## Act 5: The Closer (2:40 - 3:00)
**What to show:** Show a quick comparison slide or just the terminal.

**What to say:**
> "Omni-Dev gives *any* model a permanent memory that physically travels with your repo, saving you hours of repeating context. Zero amnesia. Thank you."
