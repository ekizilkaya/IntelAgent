import os
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("ENV_MANAGER")
ENV_PATH = os.path.abspath(".env")

@mcp.tool()
def read_env() -> str:
    """Reads the current .env file and returns its contents."""
    if not os.path.exists(ENV_PATH):
        return "No .env file found in the root directory."
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        return f.read()

@mcp.tool()
def set_env_var(key: str, value: str) -> str:
    """Sets or updates an environment variable in the .env file."""
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
    key_found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            key_found = True
            break
            
    if not key_found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")
        
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)
        
    return f"Successfully set {key} in .env file."

@mcp.tool()
def delete_env_var(key: str) -> str:
    """Deletes an environment variable from the .env file."""
    if not os.path.exists(ENV_PATH):
        return "No .env file found."
        
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    new_lines = [line for line in lines if not line.strip().startswith(f"{key}=")]
    
    if len(lines) == len(new_lines):
        return f"Key {key} not found in .env."
        
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
        
    return f"Successfully deleted {key} from .env file."

if __name__ == "__main__":
    mcp.run()
