"""
review.py - Python conversion of scratch_repo/src/commands/review.ts

The /review command performs an AI code review of recent changes.
"""
import subprocess
import os


REVIEW_PROMPT_TEMPLATE = """Please do a thorough code review of the following git diff.
Focus on:
1. Bugs or logical errors
2. Security issues
3. Performance problems
4. Code style and best practices violations
5. Missing error handling
6. Improvement suggestions

Be specific and actionable. For each issue, mention the file and line number if possible.

Git diff to review:
```diff
{diff}
```"""


async def review_command(target: str = "HEAD") -> str:
    """
    Get a git diff and return a prompt for code review.
    Mirrors review.ts from scratch_repo.
    
    Args:
        target: Git ref to diff against (default: HEAD = uncommitted changes)
    Returns:
        Prompt string to send to the agent.
    """
    try:
        # Get the diff
        if target.lower() == "staged":
            result = subprocess.run(
                ["git", "diff", "--cached"],
                capture_output=True, text=True, timeout=15, cwd=os.getcwd()
            )
        elif target.lower() in ("head", ""):
            result = subprocess.run(
                ["git", "diff", "HEAD"],
                capture_output=True, text=True, timeout=15, cwd=os.getcwd()
            )
        else:
            result = subprocess.run(
                ["git", "diff", target],
                capture_output=True, text=True, timeout=15, cwd=os.getcwd()
            )

        diff = result.stdout.strip()
        if not diff:
            # Try just the last commit
            result = subprocess.run(
                ["git", "diff", "HEAD~1", "HEAD"],
                capture_output=True, text=True, timeout=15, cwd=os.getcwd()
            )
            diff = result.stdout.strip()

        if not diff:
            return "No changes to review. Make some changes first, or specify a git ref."

        # Truncate very long diffs
        if len(diff) > 20000:
            diff = diff[:20000] + "\n\n... (diff truncated to 20,000 chars)"

        return REVIEW_PROMPT_TEMPLATE.format(diff=diff)

    except FileNotFoundError:
        return "Error: git not found. Make sure you're in a git repository."
    except subprocess.TimeoutExpired:
        return "Error: git diff timed out."
    except Exception as e:
        return f"Error running review: {e}"
