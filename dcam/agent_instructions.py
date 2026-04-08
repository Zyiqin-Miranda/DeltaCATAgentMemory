"""Agent instructions for compact context — rewires file reading behavior.

Instead of hooks (which can't modify tool output), this injects
instructions into the agent's system prompt that tell it to use
dcam for file reading: summaries first, full source only when editing.
"""

AGENT_INSTRUCTIONS = """## DCAM Compact Context Protocol

You have access to `dcam` CLI for efficient file reading. Instead of loading
full files into context, follow this protocol:

### Reading Files (ALWAYS do this first)
Instead of reading a full file, run:
```bash
dcam compact <file_path>
dcam lookup <symbol_name>
```
This gives you a summary of all functions/classes with line ranges.
Only ~1-2 lines per symbol instead of the full source.

### When You Need Full Source
Only fetch the specific chunk you need:
```bash
dcam fetch <chunk_id>
```
This returns just that function/class, not the entire file.

### When Editing
Fetch only the lines you need to modify:
```bash
dcam fetch <chunk_id>
```
Then make your edit on those specific lines.

### Workflow Example
```
# Step 1: See what's in the file (compact view)
dcam compact src/auth.py
# Output: function:login L12-45, class:AuthManager L47-120, function:logout L122-140

# Step 2: Need to understand login? Fetch just that chunk
dcam fetch 1
# Output: only lines 12-45

# Step 3: Edit login function using the line range you now know
# Make targeted edit on L12-45 only
```

### Rules
1. NEVER read an entire file with fs_read unless it's < 50 lines
2. ALWAYS run `dcam compact` first to get the summary
3. Use `dcam fetch <chunk_id>` to get only the code you need
4. Use `dcam lookup <name>` to find symbols across all indexed files
"""


def get_agent_instructions() -> str:
    """Return the agent instructions for compact context protocol."""
    return AGENT_INSTRUCTIONS
