import os
import sys
import subprocess
import uuid
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import seaborn as sns
import matplotlib.pyplot as plt
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("DataAnalyst")

WORKSPACE_DIR = os.path.abspath("./agent_workspace")

def _load_data(file_path: str) -> pd.DataFrame:
    """Helper to load CSV or Excel files."""
    if file_path.endswith('.csv'):
        return pd.read_csv(file_path)
    elif file_path.endswith(('.xls', '.xlsx')):
        return pd.read_excel(file_path)
    else:
        raise ValueError("Unsupported file format. Please provide a .csv, .xls, or .xlsx file.")

@mcp.tool()
def inspect_dataset(file_path: str) -> str:
    """
    Inspects a dataset (CSV or Excel) and returns a condensed summary.
    Includes column names, data types, row count, and the first 3 rows.
    """
    try:
        df = _load_data(file_path)
        
        row_count, col_count = df.shape
        cols_info = "\n".join([f"- {col}: {dtype}" for col, dtype in zip(df.columns, df.dtypes)])
        head_str = df.head(3).to_string()
        
        summary = (
            f"Dataset Summary for {os.path.basename(file_path)}:\n"
            f"Dimensions: {row_count} rows, {col_count} columns\n\n"
            f"Columns and Data Types:\n{cols_info}\n\n"
            f"First 3 Rows:\n{head_str}"
        )
        return summary
    except Exception as e:
        return f"Error in inspect_dataset: {str(e)}"

@mcp.tool()
def execute_sandboxed_script(code: str) -> str:
    """
    Saves the provided Python code string to a temporary .py file and runs it.
    Returns stdout on success, and stderr on error. Enforces a 300-second (5-minute) timeout.
    """
    try:
        sandbox_dir = os.path.join(WORKSPACE_DIR, "sandbox")
        os.makedirs(sandbox_dir, exist_ok=True)
        
        filename = f"script_{uuid.uuid4().hex[:8]}.py"
        filepath = os.path.join(sandbox_dir, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)
            
        import sys
        import subprocess
        
        # Determine appropriate creationflags to prevent random console popups on Windows
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW

        result = subprocess.run(
            [sys.executable, filepath],
            timeout=300,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags
        )
        
        if result.returncode == 0:
            output_str = result.stdout
        else:
            output_str = f"{result.stderr}\n{result.stdout}"
        
        with open(os.path.join(sandbox_dir, "last_tool_output.log"), "w", encoding="utf-8") as f:
            f.write(output_str)
            
        return output_str
    except subprocess.TimeoutExpired as e:
        err_msg = "TimeoutError: Script execution exceeded 300 seconds (5 minutes)."
        with open(os.path.join(sandbox_dir, "last_tool_output.log"), "w", encoding="utf-8") as f:
            f.write(err_msg)
        return err_msg
    except Exception as e:
        import traceback
        return f"Error executing sandboxed script:\n{traceback.format_exc()}"

@mcp.tool()
def install_python_package(package_name: str) -> str:
    """
    Installs a Python package in the current environment using pip.
    Use this if a script requires a library that is not currently installed.
    """
    try:
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package_name],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return f"Successfully installed {package_name}:\n{result.stdout}"
        else:
            return f"Failed to install {package_name}. Error:\n{result.stderr}\n{result.stdout}"
    except Exception as e:
        import traceback
        return f"Error installing package:\n{traceback.format_exc()}"

if __name__ == "__main__":
    # Ensure dependencies are installed (pandas, seaborn, openpyxl, etc.)
    mcp.run()