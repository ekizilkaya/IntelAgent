import asyncio
import json
import os
import hashlib
from contextlib import AsyncExitStack
from bs4 import BeautifulSoup
from openai import AsyncOpenAI
import chromadb
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import sys
from dotenv import load_dotenv

load_dotenv()

# 1. System Configuration
WORKSPACE_DIR = os.path.abspath("./agent_workspace")
MEMORY_DIR = os.path.abspath("./agent_memory")
DOWNLOADS_DIR = os.path.expanduser("~/Downloads")
PROJECTS_DIR = os.getenv("PROJECTS_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
LM_STUDIO_URL = "http://127.0.0.1:1234/v1"

# Initialize LLM Client
llm_client = AsyncOpenAI(base_url=LM_STUDIO_URL, api_key="lm-studio")

# Initialize Persistent Memory
chroma_client = chromadb.PersistentClient(path=MEMORY_DIR)
memory_collection = chroma_client.get_or_create_collection(name="research_memory")

def generate_memory_id(text: str) -> str:
    """Generates a cryptographic hash for database entries."""
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def retrieve_context(query: str, n_results: int = 2) -> str:
    """Fetches semantically relevant past interactions from ChromaDB."""
    if memory_collection.count() == 0:
        return "No prior context available."
    
    results = memory_collection.query(query_texts=[query], n_results=n_results)
    documents = []
    if results and 'documents' in results and results['documents']:
        for doc in results['documents'][0]:
            if len(doc) > 1000:
                doc = doc[:1000] + "... [Truncated by System]"
            documents.append(doc)
            
    return "\n---\n".join(documents) if documents else "No relevant context found."

def store_memory(task: str, output: str):
    """Saves the completed task and output to the vector database."""
    doc_id = generate_memory_id(output)
    memory_collection.add(
        documents=[f"Task: {task}\nResult: {output}"],
        metadatas=[{"agent": "orchestrator"}],
        ids=[f"mem_{doc_id}"]
    )

def clean_html_content(raw_html: str) -> str:
    """Parses raw HTML to extract strictly text content, discarding scripts and styling."""
    if not raw_html or "<" not in raw_html:
        return raw_html
        
    soup = BeautifulSoup(raw_html, "html.parser")
    
    # Remove script and style elements entirely
    for script_or_style in soup(["script", "style", "noscript", "meta", "link"]):
        script_or_style.extract()
        
    # Extract structural text with spacing
    text = soup.get_text(separator=' ')
    
    # Condense excess whitespace into single lines
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    cleaned_text = '\n'.join(chunk for chunk in chunks if chunk)
    
    return cleaned_text

async def execute_autonomous_research(user_task: str, max_iterations: int = 15):
    """
    Manages the multi-server MCP routing, memory retrieval, and LLM reasoning loop.
    """
    print(f"Initializing architecture for task: {user_task}\n" + "="*60)
    
    # Define MCP Servers
    server_configs = [
        StdioServerParameters(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", WORKSPACE_DIR, DOWNLOADS_DIR, PROJECTS_DIR]
        ),
        StdioServerParameters(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-puppeteer"]
        )
    ]

    async with AsyncExitStack() as stack:
        tool_to_session = {}
        openai_tools = []
        
        print("1. Booting MCP Servers (Filesystem & Puppeteer)...")
        
        # Connect to all MCP servers and aggregate their tool schemas
        for params in server_configs:
            read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            
            mcp_tools = await session.list_tools()
            
            for tool in mcp_tools.tools:
                tool_to_session[tool.name] = session
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.inputSchema
                    }
                })

        print(f"2. Aggregated {len(openai_tools)} distinct tools.")
        
        # Retrieve Memory Context
        print("3. Querying persistent memory for context...")
        historical_context = retrieve_context(user_task)
        
        system_prompt = (
            "You are an autonomous academic research agent. You have access to a local "
            "filesystem and a headless web browser. Accomplish the user's task by routing "
            "commands to your available tools. Output only factual, verified data.\n\n"
            "CRITICAL INSTRUCTION: When you have successfully executed the final step of the user's command "
            "(for example, writing the final output to a file), you MUST stop calling tools immediately. "
            "To finish, respond with a plain text message summarizing your success, and ensure no tool calls are requested."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Prior Context:\n{historical_context}\n\nCurrent Task:\n{user_task}"}
        ]

        print("4. Initiating Reasoning Loop...\n" + "-"*60)

        for iteration in range(max_iterations):
            response = await llm_client.chat.completions.create(
                model=os.getenv("LOCAL_MODEL_NAME", "mistralai/ministral-3-14b-reasoning"),
                messages=messages,
                tools=openai_tools,
                temperature=0.1,
                max_tokens=4096
            )
            
            message = response.choices[0].message
            finish_reason = response.choices[0].finish_reason
            
            # Pack the assistant message cleanly into a dictionary for local server compatibility
            assistant_msg = {
                "role": message.role or "assistant",
                "content": message.content or ""
            }
            if message.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": t.id,
                        "type": t.type,
                        "function": {
                            "name": t.function.name,
                            "arguments": t.function.arguments
                        }
                    } for t in message.tool_calls
                ]
            messages.append(assistant_msg)
            
            # If the model outputs text without calling tools, the task is complete
            if not message.tool_calls:
                content = message.content or ""
                
                # Check for incomplete output
                if not content.strip():
                    if finish_reason == "length":
                        print("[Warning] Response cut off due to length. Requesting continuation...")
                        messages.append({"role": "user", "content": "Your response was cut off. Please write out the full textual report in your next message."})
                        continue
                    else:
                        print("[Warning] Model generated empty content. Requesting actual output...")
                        messages.append({"role": "user", "content": "You did not provide any text content or tool calls. Please output the actual text report in the content field now."})
                        continue
                        
                final_output = content
                print(f"\n[Task Complete] Final Output:\n{final_output}")
                
                # Commit the result to memory
                store_memory(user_task, final_output)
                print("\n[System] Output successfully written to ChromaDB.")
                break
                
            # Process and route tool calls
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                
                try:
                    tool_args = json.loads(tool_call.function.arguments)
                    
                    # Fix url-encoded paths (e.g., %20 to single spaces)
                    import urllib.parse
                    if "path" in tool_args and isinstance(tool_args["path"], str):
                        tool_args["path"] = urllib.parse.unquote(tool_args["path"])
                        
                except json.JSONDecodeError:
                    error_msg = "System Error: Invalid JSON arguments generated by model."
                    print(f"[Error] {error_msg}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": error_msg
                    })
                    continue
                
                print(f"[Iteration {iteration + 1}] Executing: {tool_name}")
                
                target_session = tool_to_session.get(tool_name)
                
                image_parts = []
                
                if target_session:
                    try:
                        # Dispatch the command to the correct Node.js server
                        result = await target_session.call_tool(tool_name, tool_args)
                        
                        text_parts = []
                        for item in result.content:
                            if getattr(item, "type", "") == "text" or hasattr(item, "text"):
                                text_parts.append(getattr(item, "text", ""))
                            elif getattr(item, "type", "") == "image" or hasattr(item, "data"):
                                mime_type = getattr(item, "mimeType", "image/png")
                                b64_data = getattr(item, "data", "")
                                image_parts.append({
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime_type};base64,{b64_data}"}
                                })
                                
                        result_text = "\n".join(text_parts)
                        
                        # Apply intermediate parsing to browser operations
                        if "puppeteer" in tool_name and result_text:
                            original_length = len(result_text)
                            result_text = clean_html_content(result_text)
                            print(f"[Parser] Minimized payload from {original_length} to {len(result_text)} characters.")
                            
                    except Exception as e:
                        result_text = f"Tool Execution Error: {str(e)}"
                        print(f"[Error Executing {tool_name}] {e}")
                else:
                    result_text = f"System Error: Tool '{tool_name}' routing failed."
                    
                print(f"[Tool Response] {result_text[:200]}...")

                # Return the deterministic result to the LLM's context window
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_name,
                    "content": result_text if result_text else "Success"
                })
                
                # If the tool returned image data, immediately pass it to the vision model in a follow-up user message
                if image_parts:
                    messages.append({
                        "role": "user",
                        "content": [{"type": "text", "text": f"Here is the visual data returned by the `{tool_name}` tool. Please look at it and continue your task:"}] + image_parts
                    })
                    print(f"🖼️ Forwarded {len(image_parts)} image(s) to the vision model.")
                
                # Force loop break if we effectively successfully wrote the output file
                if tool_name == "write_file" and "Successfully" in result_text:
                    final_output = f"Task successfully completed. Result: {result_text}"
                    print(f"\n[Task Complete] {final_output}")
                    store_memory(user_task, final_output)
                    return
                
        if iteration == max_iterations - 1:
            print("\n[Terminated] Reached maximum tool execution iterations.")

if __name__ == "__main__":
    task = (
        "Navigate to https://en.wikipedia.org/wiki/Digital_media. "
        "Extract the text from the main introductory paragraph. "
        "Summarize that text into three rigorous bullet points. "
        "Finally, save those bullet points to a file named 'digital_media_summary.txt' "
        "in your workspace."
    )
    
    asyncio.run(execute_autonomous_research(task))