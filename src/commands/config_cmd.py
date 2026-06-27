"""
config_cmd.py - Python conversion of scratch_repo/src/commands/config.tsx

View and edit agent configuration settings.
"""
import os
import json
from typing import Dict, Any, Optional
from dotenv import set_key


CONFIG_FILE = ".omnidev_config.json"


def load_config() -> Dict[str, Any]:
    """Load config from JSON file."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(config: Dict[str, Any]):
    """Save config to JSON file."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


async def config_command(key: Optional[str] = None, value: Optional[str] = None) -> str:
    """
    View or set configuration values.
    Mirrors config.tsx from scratch_repo.
    
    Usage:
        /config              — show all config
        /config model        — show model setting
        /config model gpt-4o — set model
    """
    config = load_config()

    if key is None:
        # Show all config
        if not config and not os.environ.get("OMNI_MODEL"):
            return "No configuration set. Use /model to set a model or /api_key to set API keys."

        lines = ["## ⚙️ Current Configuration\n"]
        current_model = os.environ.get("OMNI_MODEL", "vertex_ai/gemini-1.5-pro (default)")
        lines.append(f"  🤖 Model: {current_model}")

        for k, v in config.items():
            if "key" in k.lower() or "secret" in k.lower():
                v = "***" + str(v)[-4:] if len(str(v)) > 4 else "***"
            lines.append(f"  📎 {k}: {v}")

        lines.append("\nUse `/config <key> <value>` to update a setting.")
        return "\n".join(lines)

    if value is None:
        # Show single key
        env_val = os.environ.get(key.upper(), "")
        cfg_val = config.get(key, "")
        return f"Config '{key}':\n  env: {env_val or '(not set)'}\n  file: {cfg_val or '(not set)'}"

    # Set value
    config[key] = value
    save_config(config)

    # Also try to set as environment variable
    os.environ[key.upper()] = value
    try:
        set_key(".env", key.upper(), value)
    except Exception:
        pass

    return f"✅ Config set: {key} = {value}"
