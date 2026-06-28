# Quick Start Guide - Fixed Issues

## For Gemma4 31b Users

### ✅ Correct Way
```
/model ollama/gemma4:31b
```

### ❌ Don't Do This
```
/model ollama/gemma4:31b-cloud    (Wrong format!)
```

**Note**: If you accidentally use the wrong format, it will auto-correct. But using the correct format from the start is better.

---

## For Ollama Cloud Users

### Setup (One Time)
```
/api_key 10          (Select "Ollama Cloud API Key")
Enter your API key when prompted
```

### Use Cloud Model
```
/model ollama/gemma4-cloud
```

The system automatically:
- Detects it's a cloud model
- Sets API base to `https://ollama.com`
- Validates your API key is set
- Shows a warning about tool support

---

## If Something Goes Wrong

### Option 1: Check Everything
```
/doctor
```
Shows:
- Current model & capabilities
- Ollama server status
- API keys status
- Diagnostics

### Option 2: Switch to a Working Model
```
/model gpt-4o
```

### Option 3: Reset and Try Again
```
/model ollama/gemma4:31b
(make sure `ollama serve` is running in another terminal)
```

---

## Model Capabilities Reference

### Tools Supported ✅
- `gpt-4o`
- `claude-3-5-sonnet`
- `gemini-2.5-pro`
- `groq/llama-3.3-70b-versatile`
- `vertex_ai/gemini-1.5-pro`

### No Tools ⚠️
- `ollama/*` (all local Ollama models)
- `gemma` (all variants)
- `mistral` (all variants)
- `neural-chat`
- `orca`
- `dolphin`

**Note**: Tool-limited models work fine for chatting, they just can't execute files/commands.

---

## Common Commands

| Command | Purpose |
|---------|---------|
| `/model` | Switch LLM model |
| `/api_key` | Add API key for providers |
| `/doctor` | Diagnose environment |
| `/help` | Show all commands |
| `/cost` | View token costs |
| `exit` | Quit CLI |

---

## Still Having Issues?

1. **CLI stuck/hanging**
   - Press `Ctrl+C` to interrupt
   - Run `/doctor` to diagnose
   - Try `/model gpt-4o` temporarily

2. **Ollama not working**
   - Check `ollama serve` is running
   - Run `/doctor` to see connection status
   - Try `/model gpt-4o` to confirm CLI works

3. **Cloud model not working**
   - Make sure API key is set: `/api_key 10`
   - Check with `/doctor`
   - Verify internet connection

4. **"Tool schema rejected"**
   - This is expected for tool-limited models
   - They will fallback to text-only mode
   - Model will still work fine

---

All fixes have been tested and verified. You're good to go! ✨
