import streamlit as st
import time
import asyncio
import json
import os
import hashlib
import urllib.parse
import threading
from contextlib import AsyncExitStack
from bs4 import BeautifulSoup
from openai import AsyncOpenAI
import chromadb
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# 1. System Configuration
WORKSPACE_DIR = os.path.abspath("./agent_workspace")
MEMORY_DIR = os.path.abspath("./agent_memory")
DOWNLOADS_DIR = os.path.expanduser("~/Downloads")
PROJECTS_DIR = os.getenv("PROJECTS_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
REFERENCES_DIR = os.path.abspath("./agent_references")
LM_STUDIO_URL = "http://127.0.0.1:1234/v1"

os.makedirs(REFERENCES_DIR, exist_ok=True)

# Load Skills Setup
def load_skills():
    skills_dir = os.path.abspath("./skills")
    skills = {}
    if not os.path.exists(skills_dir):
        return skills
        
    for skill_folder in os.listdir(skills_dir):
        skill_path = os.path.join(skills_dir, skill_folder)
        if not os.path.isdir(skill_path):
            continue
            
        manifest_path = os.path.join(skill_path, "manifest.json")
        instructions_path = os.path.join(skill_path, "instructions.md")
        
        if os.path.exists(manifest_path):
            with open(manifest_path, "r", encoding="utf-8-sig") as f:
                manifest = json.load(f)
                
            role_description = ""
            if os.path.exists(instructions_path):
                with open(instructions_path, "r", encoding="utf-8-sig") as f:
                    role_description = f.read()
                    
            manifest["role_description"] = role_description
            skills[manifest["id"]] = manifest
            
    return skills

REGISTERED_SKILLS = load_skills()

# Agent display names and icons
AGENT_LABELS = {
    skill_id: (skill["icon"], skill["name"]) 
    for skill_id, skill in REGISTERED_SKILLS.items()
}

# Initialize LLM Client
llm_client = AsyncOpenAI(base_url=LM_STUDIO_URL, api_key="lm-studio")

# Initialize Persistent Memory
try:
    chroma_client = chromadb.PersistentClient(path=MEMORY_DIR)
    memory_collection = chroma_client.get_or_create_collection(name="research_memory")
    references_collection = chroma_client.get_or_create_collection(name="references")
except Exception as e:
    st.error(f"Error initializing database: {e}")

# Thread-safe stop flag
_stop_lock = threading.Lock()

def request_stop():
    with _stop_lock:
        st.session_state["_agent_stop_requested"] = True

def is_stop_requested() -> bool:
    with _stop_lock:
        return st.session_state.get("_agent_stop_requested", False)

def clear_stop():
    with _stop_lock:
        st.session_state["_agent_stop_requested"] = False

def generate_memory_id(text: str) -> str:
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def retrieve_context(query: str, n_results: int = 2) -> str:
    if memory_collection.count() == 0:
        return "No prior context available."
    results = memory_collection.query(query_texts=[query], n_results=n_results)
    
    documents = []
    if results and 'documents' in results and results['documents'] and results['documents'][0]:
        docs = results['documents'][0]
        distances = results.get('distances', [[0.0]*len(docs)])[0]
        for doc, dist in zip(docs, distances):
            # Truncate extremely long docs so they don't overpower the prompt
            if len(doc) > 1000:
                doc = doc[:1000] + "... [Truncated by System]"
            documents.append(doc)
            
    return "\n---\n".join(documents) if documents else "No relevant context found."

def store_memory(task: str, output: str):
    doc_id = generate_memory_id(output)
    memory_collection.add(
        documents=[f"Task: {task}\nResult: {output}"],
        metadatas=[{"agent": "orchestrator"}],
        ids=[f"mem_{doc_id}"]
    )

def clean_html_content(raw_html: str) -> str:
    if not raw_html or "<" not in raw_html:
        return raw_html
    soup = BeautifulSoup(raw_html, "html.parser")
    for script_or_style in soup(["script", "style", "noscript", "meta", "link"]):
        script_or_style.extract()
    text = soup.get_text(separator=' ')
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    return '\n'.join(chunk for chunk in chunks if chunk)

async def route_task(user_task: str, client: AsyncOpenAI, model: str) -> str:
    """Evaluates the user's task and routes it to the specific sub-agent."""
    if not REGISTERED_SKILLS:
        return "WEB_SEARCHER"
        
    capabilities_list = "\n".join([
        f"- {skill['id']}: {skill.get('routing_description', '')}" 
        for skill in REGISTERED_SKILLS.values()
    ])
    
    valid_ids = list(REGISTERED_SKILLS.keys())
    valid_ids_str = "\n".join(valid_ids)
    
    prompt = f"""You are a Manager Agent mapping tasks to specialized sub-agents.
Route the task to exactly ONE of these specialized sub-agents based on their capabilities:

{capabilities_list}

Respond with EXACTLY ONE of the following words, with no other text or punctuation:
{valid_ids_str}

Task: {user_task}
"""
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        decision = response.choices[0].message.content.strip().upper()
        for valid_agent in valid_ids:
            if valid_agent in decision:
                return valid_agent
        return valid_ids[0]
    except Exception:
        return valid_ids[0] if valid_ids else "WEB_SEARCHER"

async def run_agent(user_task: str, ui_status, chat_history: list, client: AsyncOpenAI, model: str, result_placeholder=None, stop_placeholder=None, progress_placeholder=None, max_iterations: int = 30) -> str:
    agent_start_time = time.time()
    total_input_tokens = 0
    total_output_tokens = 0

    ui_status.update(label="Analyzing task (Manager Assistant)..." if st.session_state.lang == "EN" else "Görev analiz ediliyor (Yönetici Asistan)...", state="running")
    agent_type = await route_task(user_task, client, model)
    # Store agent type so the UI can show a badge
    st.session_state["_last_agent_type"] = agent_type
    ui_status.write(f"🔀 Task routed to expert: **{agent_type}**" if st.session_state.lang == "EN" else f"🔀 Görev şu uzmana yönlendirildi: **{agent_type}**")
    
    skill_config = REGISTERED_SKILLS.get(agent_type, {})
    server_configs = []
    
    for mcp in skill_config.get("mcp_servers", []):
        env = {**os.environ} if mcp.get("pass_env") else None
        
        # Replace template placeholders in args
        args = []
        for arg in mcp.get("args", []):
            if "{WORKSPACE_DIR}" in arg: arg = arg.replace("{WORKSPACE_DIR}", WORKSPACE_DIR)
            if "{DOWNLOADS_DIR}" in arg: arg = arg.replace("{DOWNLOADS_DIR}", DOWNLOADS_DIR)
            if "{PROJECTS_DIR}" in arg: arg = arg.replace("{PROJECTS_DIR}", PROJECTS_DIR)
            args.append(arg)
            
        cmd = mcp.get("command", "")
        if "{sys.executable}" in cmd:
            cmd = cmd.replace("{sys.executable}", sys.executable)
            
        server_configs.append(
            StdioServerParameters(
                command=cmd, 
                args=args,
                env=env
            )
        )
        
    role_description = skill_config.get("role_description", "")

    async with AsyncExitStack() as stack:
        tool_to_session = {}
        openai_tools = []
        
        ui_status.update(label="Starting MCP Servers..." if st.session_state.lang == "EN" else "MCP Sunucuları Başlatılıyor...", state="running")
        for params in server_configs:
            try:
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
            except Exception as e:
                ui_status.write(f"⚠️ Server startup error {params.command}: {e}" if st.session_state.lang == "EN" else f"⚠️ Sunucu başlatma hatası {params.command}: {e}")

        ui_status.write(f"✅ {len(openai_tools)} different tools assembled." if st.session_state.lang == "EN" else f"✅ {len(openai_tools)} farklı araç bir araya getirildi.")
        
        ui_status.update(label="Querying persistent memory..." if st.session_state.lang == "EN" else "Kalıcı bellek sorgulanıyor...", state="running")
        historical_context = retrieve_context(user_task)
        
        session_workspace_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        session_workspace_path = os.path.join(WORKSPACE_DIR, session_workspace_name)
        os.makedirs(session_workspace_path, exist_ok=True)
        
        system_prompt = (
            f"You are an autonomous {role_description}. "
            "Accomplish the user's task by routing commands to your available tools.\n\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. Output only factual, verified data.\n"
            "2. When asked to perform data analysis, create visualizations, ALWAYS use your built-in DataAnalyst tools "
            "(`inspect_dataset`, `execute_sandboxed_script`) directly. DO NOT write Python scripts using `write_file` "
            "unless the user explicitly asks for a .py file! If you need to generate a plot or analyze data, pass the Python code directly into `execute_sandboxed_script`.\n"
            "   - IMPORTANT: When writing visualization code, ALWAYS prefer using modern visualization libraries like seaborn, plotly, or altair rather than plain matplotlib to ensure the charts look professional and modern.\n"
            "3. When generating long reports, YOU MUST use the `write_file` tool to save them. However, due to your strict Context Window limits, trying to write an entire massive report in ONE `write_file` call will cause you to crash! Instead, ALWAYS write the report in smaller split files (e.g., `report_part1_summary.md`, `report_part2_findings.md`, etc.). Limit your outputs to 500-1000 words per tool call.\n"
            "4. When you have successfully executed the final step of the user's command "
            "(for example, saving a visualization or writing final output to a file), you MUST stop calling tools immediately. "
            "To finish, respond with a plain text message summarizing your success, and ensure no tool calls are requested.\n"
            "5. NEVER get stuck in an endless loop. If your searches are not returning the exact results you want, synthesize the best available information and stop searching. Maximum 2 or 3 searches are enough for most tasks.\n"
            f"6. IMPORTANT: Your dedicated workspace path for this session is '{session_workspace_path}'. Unless specifically asked to save elsewhere, ALWAYS save your final generated files (Markdown, CSV, JSON, PNG, etc.) in this dedicated directory."
        )

        messages = [{"role": "system", "content": system_prompt}]
        
        # Add recent conversation history (max 4 messages to avoid context bleed)
        recent_history = [msg for msg in chat_history[:-1] if msg.get("role") != "tool"][-4:]
        for msg in recent_history:
            messages.append({"role": msg["role"], "content": msg["content"]})
            
        # Only inject historical context if this is a substantial new task, not a tiny follow-up
        if len(user_task) > 15:
            context_section = f"<Prior Context from DB>\n{historical_context}\n</Prior Context>\n\n"
        else:
            context_section = ""

        messages.append({"role": "user", "content": f"{context_section}<Current Task>\n{user_task}\n</Current Task>\n\nFocus ONLY on the Current Task."})

        ui_status.update(label="Starting Reasoning Loop..." if st.session_state.lang == "EN" else "Akıl Yürütme Döngüsü Başlatılıyor...", state="running")

        executed_tool_calls = set()

        for iteration in range(max_iterations):
            # Check stop flag
            if is_stop_requested():
                clear_stop()
                stop_msg = "⛔ Task stopped by user."
                ui_status.update(label="Stopped." if st.session_state.lang == "EN" else "Durduruldu.", state="error")
                if stop_placeholder:
                    stop_placeholder.empty()
                if result_placeholder:
                    result_placeholder.warning(stop_msg)
                st.session_state.chat_history.append({"role": "assistant", "content": stop_msg})
                return stop_msg

            # Update progress bar
            if progress_placeholder is not None:
                progress_placeholder.progress((iteration) / max_iterations, text=f"Iteration {iteration + 1} / {max_iterations}" if st.session_state.lang == "EN" else f"İterasyon {iteration + 1} / {max_iterations}")

            if iteration == max_iterations - 5:
                warning_msg = "System Warning: Approaching maximum step limit. Please stop researching and use the write_file tool to quickly save the report with your current information." if st.session_state.lang == "EN" else "Sistem Uyarı: Maksimum adım sınırına yaklaşıyorsunuz. Lütfen daha fazla araştırma yapmayı bırakın ve mevcut bilgilerinizle write_file aracını kullanarak hedeflenen raporu hızla kaydedin."
                ui_status.write(f"⚠️ {warning_msg}")
                messages.append({"role": "user", "content": "SYSTEM CRITICAL WARNING: You are 5 steps away from reaching the maximum iteration limit. You MUST STOP gathering information, exploring, or searching immediately! Start synthesizing the information you currently have and use the `write_file` tool to save your comprehensive final report now. Do not call any further search tools."})

            import openai
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=openai_tools,
                    temperature=0.1,
                    max_tokens=4096
                )
            except Exception as e:
                # Catch image processing errors from text-only models
                if "failed to process image" in str(e).lower() or "image" in str(e).lower():
                    error_msg = "System Error: Image processing failed. The model might be text-only. Skipping image and continuing..." if st.session_state.lang == "EN" else "Sistem Hatası: Görüntü işleme başarısız. Model metin tabanlı olabilir. Görüntü atlanarak devam ediliyor..."
                    ui_status.write(f"⚠️ {error_msg}")
                    # Remove the last user message containing the image parts
                    if messages and messages[-1].get("role") == "user" and isinstance(messages[-1].get("content"), list):
                        messages.pop()
                        messages.append({"role": "user", "content": "The screenshot was saved securely to the disk, but the vision model could not process the image array. The task can be considered successful if saving was the only requirement."})
                        response = await client.chat.completions.create(
                            model=model,
                            messages=messages,
                            tools=openai_tools,
                            temperature=0.1
                        )
                    else:
                        raise e
                else:
                    raise e
                    
            # Update token usage stats
            if hasattr(response, 'usage') and response.usage:
                total_input_tokens += getattr(response.usage, 'prompt_tokens', 0)
                total_output_tokens += getattr(response.usage, 'completion_tokens', 0)

            message = response.choices[0].message

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
            
            # If there's no tool calls, it's a final answer or termination
            if not message.tool_calls:
                # Include reasoning_content if the model provides it
                reasoning = getattr(message, "reasoning_content", "") or ""
                if not reasoning and hasattr(message, "model_extra") and message.model_extra:
                    reasoning = message.model_extra.get("reasoning_content", "") or ""
                
                content = message.content or ""
                
                # If finish reason is length, try to append the tool call if one was cut off
                finish_reason = response.choices[0].finish_reason if hasattr(response.choices[0], "finish_reason") else "stop"
                
                if finish_reason == "length":
                    msg = "System Warning: Response cut off due to token limit. Prompting model to continue from where it left off..." if st.session_state.lang == "EN" else "Sistem Uyarı: Yanıt token sınırına ulaştığı için kesildi. Modelden kaldığı yerden devam etmesi isteniyor..."
                    ui_status.write(f"⚠️ {msg}")
                    messages.append({"role": "user", "content": "SYSTEM WARNING: Your previous response was cut off because you reached the maximum token limit. If you were trying to write a massive file using a tool call, DO NOT try to write the entire report at once! It is physically impossible to fit it. Instead, write just the FIRST PART of your report using the `write_file` tool (e.g., save it as `report_part1.md`), and then wait. In the next iterations, you can write `report_part2.md`, etc. Break your work down into smaller chunks!"})
                    continue
                
                # Check for incomplete output or empty content
                if not content.strip():
                    msg = "System Warning: Model returned empty content and no tool calls. Prompting to generate report..." if st.session_state.lang == "EN" else "Sistem Uyarı: Model boş bir içerik ve araç çağrısı olmadan döndü. Raporu oluşturması isteniyor..."
                    ui_status.write(f"⚠️ {msg}")
                    messages.append({"role": "user", "content": "You generated reasoning but no final answer or tool calls. If you are ready to conclude, please output the full markdown report in your standard content field now, or use the write_file tool to save it."})
                    continue
                
                final_output = f"**Reasoning:**\n{reasoning}\n\n**Answer:**\n{content}".strip()
                
                if not reasoning.strip() and not content.strip():
                    final_output = f"Task completed successfully without further output. Message Summary:\n```python\n{repr(message)}\n```"
                
                # Update status
                ui_status.update(label=t("Task Completed!", "Görev Tamamlandı!"), state="complete")
                
                if progress_placeholder is not None:
                    progress_placeholder.progress(1.0, text="Completed!" if st.session_state.lang == "EN" else "Tamamlandı!")
                
                if stop_placeholder:
                    stop_placeholder.empty()
                
                end_time = time.time()
                elapsed = end_time - agent_start_time
                mins = int(elapsed // 60)
                secs = int(elapsed % 60)
                time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                
                tot_tokens = total_input_tokens + total_output_tokens
                cost = (total_input_tokens / 1_000_000) * 0.50 + (total_output_tokens / 1_000_000) * 3.00
                
                metadata = {
                    "time_str": time_str,
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "total_tokens": tot_tokens,
                    "cost": cost
                }

                # UI'ya daha çıkış aşamasını beklemeden (örneğin Puppeteer MCP timeout yaparsa) yansıt.
                if result_placeholder:
                    container = result_placeholder.container()
                    container.markdown(final_output)
                    with container.expander("📋 Yanıtı Kopyala / Raw Markdown" if st.session_state.lang == "TR" else "📋 Copy Response / Raw Markdown"):
                        st.code(final_output, language="markdown")

                    metrics_html = f"""
                    <div style="display: flex; gap: 15px; font-size: 0.8rem; color: #888; margin-top: 10px; padding: 8px 12px; background: rgba(0,0,0,0.1); border-radius: 6px; border: 1px solid rgba(255,255,255,0.05);">
                        <div>⏱️ {time_str}</div>
                        <div>🪙 {tot_tokens:,} tokens (In: {total_input_tokens:,} | Out: {total_output_tokens:,})</div>
                        <div>💵 ${cost:.4f}</div>
                    </div>
                    """
                    container.markdown(metrics_html, unsafe_allow_html=True)
                    
                st.session_state.chat_history.append({
                    "role": "assistant", 
                    "content": final_output,
                    "metadata": metadata
                })

                store_memory(user_task, final_output)
                print(f"\n[DEBUG] Final Output Returned from run_agent:\n{final_output}\n")
                return final_output
                
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                
                try:
                    tool_args = json.loads(tool_call.function.arguments)
                    if "path" in tool_args and isinstance(tool_args["path"], str):
                        tool_args["path"] = urllib.parse.unquote(tool_args["path"])
                    
                    # Intercept JSON strings that LLMs sometimes pass as content instead of pure plain text
                    if tool_name == "write_file" and "content" in tool_args and isinstance(tool_args["content"], dict):
                        tool_args["content"] = json.dumps(tool_args["content"], indent=2, ensure_ascii=False)
                        
                except json.JSONDecodeError:
                    error_msg = "System Error: Invalid JSON arguments generated by the model."
                    ui_status.write(f"❌ {error_msg}")
                    messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": tool_name, "content": error_msg})
                    continue

                tool_signature = f"{tool_name}//{json.dumps(tool_args, sort_keys=True)}"
                if tool_signature in executed_tool_calls:
                    duplicate_msg = f"[SYSTEM WARNING: You have already executed this EXACT tool query. Do not repeat identical searches! You must synthesize what you have or use the write_file tool to finalize the report.]"
                    ui_status.write("⚠️ Same tool call repeated, blocked by system." if st.session_state.lang == "EN" else "⚠️ Aynı araç çağrısı tekrarlandı, sistem tarafından engellendi.")
                    messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": tool_name, "content": duplicate_msg})
                    continue
                executed_tool_calls.add(tool_signature)

                # Execute the tool and fetch results
                ui_status.write(f"**Iteration {iteration + 1}**: running `{tool_name}`..." if st.session_state.lang == "EN" else f"**İterasyon {iteration + 1}**: `{tool_name}` çalıştırılıyor...")
                target_session = tool_to_session.get(tool_name)
                
                image_parts = []
                
                if target_session:
                    try:
                        # Ensure tools don't hang indefinitely (e.g. puppeteer scraping)
                        result = await asyncio.wait_for(
                            target_session.call_tool(tool_name, tool_args),
                            timeout=120.0
                        )
                        
                        text_parts = []
                        for item in result.content:
                            if getattr(item, "type", "") == "text" or hasattr(item, "text"):
                                text_parts.append(getattr(item, "text", ""))
                            elif getattr(item, "type", "") == "image" or hasattr(item, "data"):
                                mime_type = getattr(item, "mimeType", "image/png")
                                b64_data = getattr(item, "data", "")
                                
                                import base64
                                img_idx = len(image_parts) + 1
                                save_path = os.path.join(session_workspace_path, f"{tool_name}_{iteration}_{img_idx}.png")
                                try:
                                    with open(save_path, "wb") as f:
                                        f.write(base64.b64decode(b64_data))
                                    text_parts.append(f"[System: Screenshot successfully saved to {save_path}.]")
                                except Exception as img_e:
                                    text_parts.append(f"[System: Failed to save image: {img_e}]")

                                image_parts.append({
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime_type};base64,{b64_data}"}
                                })
                                
                        result_text = "\n".join(text_parts)
                        
                        if "puppeteer" in tool_name and result_text:
                            result_text = clean_html_content(result_text)
                            
                        # Limit output size to prevent context overflow and model confusion
                        if len(result_text) > 12000:
                            result_text = result_text[:12000] + "\n\n... [SYSTEM WARNING: The result was TRUNCATED because it was too long (over 12000 chars). DO NOT repeat the exact same tool call! You must synthesize what you see here, use smaller counts/pagination, or refine your query entirely.]"
                            
                    except Exception as e:
                        result_text = f"Tool Execution Error: {str(e)}"
                        ui_status.write(f"❌ Error: {e}" if st.session_state.lang == "EN" else f"❌ Hata: {e}")
                else:
                    result_text = f"System Error: Failed to route to tool '{tool_name}'."
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_name,
                    "content": result_text if result_text else "Success"
                })
                
                if image_parts:
                    messages.append({
                        "role": "user",
                        "content": [{"type": "text", "text": f"Here is the visual data returned by the `{tool_name}` tool. Please look at it and continue your task:"}] + image_parts
                    })
                    ui_status.write(f"🖼️ {len(image_parts)} images passed to vision model." if st.session_state.lang == "EN" else f"🖼️ {len(image_parts)} görüntü görme modeline iletildi.")
                
                ui_status.write(f"➡️ `{tool_name}` successfully completed." if st.session_state.lang == "EN" else f"➡️ `{tool_name}` başarıyla tamamlandı.")
                
                # Check stop after each tool call
                if is_stop_requested():
                    break
                
                # Intercept logical completion since some open source models struggle to break loops naturally
                # We only want to auto-exit if the model isn't planning to do more. 
                # Better to just let the LLM decide when it's done naturally based on the system prompt, rather than hardcoding an exit on first write.
                # Removing the auto-exit on write_file to allow multi-step tasks.
                if tool_name in ["write_file", "generate_visualization"] and "Successfully" in result_text:
                    ui_status.write("✨ File written successfully! Checking for next steps..." if st.session_state.lang == "EN" else "✨ Dosya başarıyla yazıldı! Diğer adımlar kontrol ediliyor...")
                
        error_msg = "Task could not be completed within the iteration limit." if st.session_state.lang == "EN" else "Görev iterasyon sınırı içinde tamamlanamadı."
        ui_status.update(label="Terminated: Maximum iteration limit reached." if st.session_state.lang == "EN" else "Sonlandırıldı: Maksimum iterasyon sınırına ulaşıldı.", state="error")
        if result_placeholder:
            result_placeholder.error(error_msg)
        return error_msg

# --- Streamlit UI Configuration ---
if "lang" not in st.session_state:
    st.session_state.lang = "EN"

HU_TRANSLATIONS = {
    "Task Completed!": "Feladat befejezve!",
    "How To Use": "Használati útmutató",
    "Close Guide": "Útmutató bezárása",
    "An open-source AI assistant for research, copyediting, data extraction, analysis, visualization, news discovery and document management. A secure, automated workspace featuring optional local LLMs, built by and for journalists and researchers.": "Nyílt forráskódú AI asszisztens kutatáshoz, szöveggondozáshoz, adatkinyeréshez, elemzéshez, vizualizációhoz, hírkereséshez és dokumentumkezeléshez. Biztonságos, automatizált munkaterület opcionális helyi LLM-ekkel, újságírók és kutatók által, újságírók és kutatók számára fejlesztve.",
    "Welcome! Model Selection": "Üdvözöljük! Modellválasztás",
    "Which model would you like to use?": "Melyik modellt szeretné használni?",
    "Local Model (LM Studio)": "Helyi modell (LM Studio)",
    "Start": "Indítás",
    "⏳ Starting server...": "⏳ Szerver indítása...",
    "✅ Server is ready!": "✅ Szerver készen áll!",
    "Language": "Nyelv",
    "Model Settings": "Modell beállítások",
    "Use OpenRouter": "OpenRouter használata",
    "Max Iterations": "Max iterációk",
    "Memory": "Memória",
    "Upload Documents": "Dokumentumok feltöltése",
    "Upload target:": "Feltöltés célja:",
    "Upload files": "Fájlok feltöltése",
    "Everything": "Minden",
    "Generated Outputs Only": "Csak generált kimenetek",
    "Uploaded Inputs Only": "Csak feltöltött bemenetek",
    "🗑️ Clean Sandbox": "🗑️ Homokozó tisztítása",
    "🗑️ Clear History": "🗑️ Előzmények törlése",
    "🧹 Clear Memory": "🧹 Memória törlése",
    "Current Task:": "Jelenlegi feladat:",
    "Task Summary:": "Feladat összefoglalása:",
    "Start Agent": "Ügynök indítása",
    "Start Server and Load Model": "Szerver indítása és modell betöltése",
    "Pause": "Szünet",
    "⛔ Stop Process": "⛔ Folyamat leállítása",
    "Cleanup": "Tisztítás",
    "Workspace": "Munkaterület",
    "Library (Reference Documents)": "Könyvtár (Referencia Dokumentumok)",
    "Library (References)": "Könyvtár (Hivatkozások)",
    "🗑️ Clean Old Workspaces": "🗑️ Régi munkaterületek törlése",
    "Export": "Exportálás",
    "📥 Download Chat as Markdown": "📥 Csevegés letöltése Markdown-ként",
    "📋 Copy Response / Raw Markdown": "📋 Válasz másolása / Raw Markdown",
    "Workspace Data (Active Task)": "Munkaterület adatok (Aktív feladat)",
    "Saved Memory Count": "Mentett memóriák száma",
    "Indexed Chunks Count": "Indexelt darabok száma",
    "What would you like me to do?": "Mit szeretne, mit tegyek?",
    "♻️ Index References": "♻️ Hivatkozások indexelése",
    "Indexing files... (This might take a while)": "Fájlok indexelése... (Ez eltarthat egy ideig)",
    "All documents successfully indexed! Rerunning...": "Minden dokumentum sikeresen indexelve! Újrafuttatás...",
    "Required libraries are missing. Run 'pip install PyMuPDF sentence-transformers' in the terminal.": "Szükséges könyvtárak hiányoznak. Futtassa: 'pip install PyMuPDF sentence-transformers'",
    "Nothing to clean.": "Nincs mit tisztítani.",
    "What to clean?": "Mit tisztítsunk?",
    "files": "fájlok",
    "No chat to export yet.": "Még nincs exportálható csevegés.",
    "Workspace is empty.": "A munkaterület üres.",
    "Memory cleared!": "Memória törölve!",
    "Execution Failed": "Végrehajtás sikertelen",
    "Old workspaces cleaned!": "Régi munkaterületek megtisztítva!",
    "No old workspaces found.": "Nem találhatók régi munkaterületek.",
    "Cleaned some files, but a few remain (they might be in use).": "Néhány fájl törölve, de pár megmaradt (talán használatban vannak).",
    "Cleans old session folders in agent_workspace": "Törli a régi munkaterület mappákat",
    "Copy PDF/TXT files to the agent_references/ folder.": "Másolja a PDF/TXT fájlokat az agent_references/ mappába.",
    "Maximum number of steps the agent will perform for a single task.": "A maximális lépések száma, amit az ügynök egy feladathoz elvégez.",
    "Please enter an OpenRouter API Key (or add OPENROUTER_KEY to .env).": "Kérjük, adjon meg egy OpenRouter API kulcsot (vagy adja hozzá az OPENROUTER_KEY-t a .env-hez).",
    "Agent Settings": "Ügynök beállítások",
    "Starting Background Agent...": "Háttér ügynök indítása..."
}

def t(en_text, tr_text):
    if st.session_state.lang == "EN":
        return en_text
    if st.session_state.lang == "TR":
        return tr_text
    if st.session_state.lang == "HU":
        if en_text in HU_TRANSLATIONS:
            return HU_TRANSLATIONS[en_text]
        import re
        if "Failed to start model" in en_text:
            return en_text.replace("Failed to start model", "Nem sikerült elindítani a modellt")
        if "Loading" in en_text and "model into" in en_text:
            m = re.search(r"Loading (.*?) model into VRAM", en_text)
            if m: return en_text.replace(f"Loading {m.group(1)} model into VRAM, please wait...", f"{m.group(1)} modell betöltése VRAM-ba, kérjük várjon...")
        if "When the local model is selected" in en_text:
            m = re.search(r"and the (.*?) model will be loaded", en_text)
            if m: return f"Amikor a helyi modell van kiválasztva, az LM Studio szerver elindul a háttérben, és a {m.group(1)} modell betöltésre kerül."
        if "file(s) uploaded!" in en_text:
            return en_text.replace("file(s) uploaded!", "fájl feltöltve!")
        if "Sandbox cleaned:" in en_text:
            return en_text.replace("Sandbox cleaned:", "Homokozó tisztítva:")
        if "Error during indexing:" in en_text:
            return en_text.replace("Error during indexing:", "Hiba az indexelés során:")
        if "An error occurred:" in en_text:
            return en_text.replace("An error occurred:", "Hiba történt:")
        return en_text
    return en_text

st.set_page_config(page_title="IntelAgent", page_icon="🤖", layout="wide", initial_sidebar_state="collapsed")

import base64
def get_base64_of_bin_file(bin_file):
    with open(bin_file, 'rb') as f:
        data = f.read()
    return base64.b64encode(data).decode()

bg_img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "background.jpg")
try:
    bin_str = get_base64_of_bin_file(bg_img_path)
    bg_img_style = f'''
    <style>
    .stApp {{
        background-image: url("data:image/jpeg;base64,{bin_str}");
        background-size: cover;
        background-position: center;
        background-repeat: no-repeat;
    }}
    </style>
    '''
    st.markdown(bg_img_style, unsafe_allow_html=True)
except Exception as e:
    st.warning(f"Background image not found or could not be loaded: {e}")

# --- Custom CSS for modern look ---
st.markdown("""
<style>
/* Subtle gradient header area */
header[data-testid="stHeader"] {
    background: linear-gradient(90deg, #0e1117 0%, #1a1c24 100%);
}

/* Chat message styling */
[data-testid="stChatMessage"] {
    border-radius: 12px;
    margin-bottom: 0.5rem;
}

/* Agent badge pill */
.agent-badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.3px;
    margin-bottom: 4px;
}
.agent-badge.web { background: #1e3a5f; color: #7ec8e3; }
.agent-badge.visual { background: #3b2a50; color: #c9a0dc; }
.agent-badge.local { background: #2a3f2a; color: #90d890; }
.agent-badge.data { background: #4a3a1e; color: #f0c060; }
.agent-badge.academic { background: #1e2a4a; color: #a0b8e0; }
.agent-badge.librarian { background: #4a2a2a; color: #e0a0a0; }

/* Sidebar section headers */
.sidebar-section {
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #888;
    margin-top: 1.2rem;
    margin-bottom: 0.3rem;
    border-bottom: 1px solid #333;
    padding-bottom: 4px;
}

/* Progress bar custom color */
[data-testid="stProgress"] > div > div > div {
    background: linear-gradient(90deg, #4a9ced, #7c5cbf);
}
</style>
""", unsafe_allow_html=True)

st.title("🤖 IntelAgent")
st.caption(t("An open-source AI assistant for research, copyediting, data extraction, analysis, visualization, news discovery and document management. A secure, automated workspace featuring optional local LLMs, built by and for journalists and researchers.", "Araştırma, redaksiyon, veri çıkarımı, analiz, görselleştirme, haber keşfi ve belge yönetimi için açık kaynaklı bir yapay zeka asistanı. İsteğe bağlı yerel YZ modelleri (LLM'ler) sunan, gazeteciler ve araştırmacılar tarafından ve onlar için tasarlanmış güvenli, otomatikleştirilmiş bir çalışma alanı."))

if st.session_state.get("show_readme", False):
    with st.container(border=True):
        col1, col2 = st.columns([0.9, 0.1])
        with col1:
            st.subheader("📖 " + t("How To Use", "Rehber"))
        with col2:
            if st.button("❌", key="btn_close_readme_top"):
                st.session_state.show_readme = False
                st.rerun()
        
        try:
            with open("readme.md", "r", encoding="utf-8") as f:
                st.markdown(f.read())
        except Exception:
            st.error("readme.md not found.")
            
        if st.button(t("Close Guide", "Rehberi Kapat"), key="btn_close_readme_bottom"):
            st.session_state.show_readme = False
            st.rerun()

# ── Başlangıç (Server/Model Kurulumu) ─────────────────────────
if "server_ready" not in st.session_state:
    st.session_state.server_ready = False

if not st.session_state.server_ready:
    st.subheader(t("Welcome! Model Selection", "Hoş Geldiniz! Model Seçimi"))
    
    use_openrouter = st.radio(t("Which model would you like to use?", "Hangi modeli kullanmak istersiniz?"), (t("Local Model (LM Studio)", "Yerel Model (LM Studio)"), "OpenRouter")) == "OpenRouter"
    
    if use_openrouter:
        openrouter_model = st.text_input("OpenRouter Model", value="mistralai/mistral-large-2512")
        
        if st.button(t("Start", "Başlat")):
            st.session_state.use_openrouter = True
            st.session_state.openrouter_model = openrouter_model
            st.session_state.server_ready = True
            st.rerun()
    else:
        local_model_name = os.getenv("LOCAL_MODEL_NAME", "mistralai/ministral-3-14b-reasoning")
        st.info(t(f"When the local model is selected, the LM Studio server will start in the background and the {local_model_name} model will be loaded.", f"Yerel model seçildiğinde, arka planda LM Studio sunucusu başlatılacak ve {local_model_name} modeli yüklenecektir."))
        if st.button(t("Start Server and Load Model", "Sunucuyu Başlat ve Modeli Yükle")):
            status_container = st.empty()
            import subprocess
            import time
            try:
                status_container.info(t("⏳ Starting server...", "⏳ Sunucu başlatılıyor..."))
                subprocess.Popen(["lms", "server", "start"], shell=True)
                time.sleep(3)
                
                status_container.info(t(f"⏳ Loading {local_model_name} model into VRAM, please wait...", f"⏳ {local_model_name} modeli VRAM\'e yükleniyor, lütfen bekleyin..."))
                subprocess.run(["lms", "load", local_model_name], shell=True, check=True)
                
                status_container.success(t("✅ Server is ready!", "✅ Sunucu hazır!"))
                time.sleep(1)
                st.session_state.use_openrouter = False
                st.session_state.server_ready = True
                st.rerun()
            except Exception as e:
                status_container.error(t(f"❌ Failed to start model: {e}", f"❌ Model başlatılamadı: {e}"))
    
    # Uygulamanın geri kalanını yüklememek için burada durdur
    st.stop()


# ── Sidebar ──────────────────────────────────────────────
with st.sidebar:
    if st.button("📖 " + t("How To Use", "Rehber"), use_container_width=True):
        st.session_state.show_readme = not st.session_state.get("show_readme", False)
        st.rerun()
        
    st.markdown(f'<div class="sidebar-section">{t("Language", "Dil")}</div>', unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("EN", use_container_width=True):
            st.session_state.lang = "EN"
            st.rerun()
    with col2:
        if st.button("TR", use_container_width=True):
            st.session_state.lang = "TR"
            st.rerun()
    with col3:
        if st.button("HU", use_container_width=True):
            st.session_state.lang = "HU"
            st.rerun()

    # --- Model / Provider ---
    st.markdown(f'<div class="sidebar-section">{t("Model Settings", "Model Ayarları")}</div>', unsafe_allow_html=True)
    use_openrouter = st.checkbox(t("Use OpenRouter", "OpenRouter Kullan"), value=st.session_state.get('use_openrouter', False))
    st.session_state.use_openrouter = use_openrouter
    
    if use_openrouter:
        openrouter_api_key = os.environ.get("OPENROUTER_KEY") or st.session_state.get('openrouter_api_key', '')
        # Arka plandan geldiyse yine de kaydet (istenirse input'ta da gösterilebilir, opsiyonel)
        if not openrouter_api_key:
            openrouter_api_key = st.text_input("OpenRouter API Key", type="password", value="")
        
        st.session_state.openrouter_api_key = openrouter_api_key
        openrouter_model = st.text_input("OpenRouter Model", value=st.session_state.get('openrouter_model', 'google/gemini-3-flash-preview'))
        st.session_state.openrouter_model = openrouter_model
        
        if openrouter_api_key:
            active_client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=openrouter_api_key)
            active_model = openrouter_model
        else:
            local_model_name = os.getenv("LOCAL_MODEL_NAME", "mistralai/ministral-3-14b-reasoning")
            st.warning(t("Please enter an OpenRouter API Key (or add OPENROUTER_KEY to .env).", "Lütfen bir OpenRouter API Key girin (veya .env içine OPENROUTER_KEY ekleyin)."))
            active_client = llm_client
            active_model = local_model_name
    else:
        local_model_name = os.getenv("LOCAL_MODEL_NAME", "mistralai/ministral-3-14b-reasoning")
        active_client = llm_client
        active_model = local_model_name

    # --- Agent Settings ---
    st.markdown(f'<div class="sidebar-section">{t("Agent Settings", "Ajan Ayarları")}</div>', unsafe_allow_html=True)
    max_iterations = st.slider(t("Max Iterations", "Maks. İterasyon"), min_value=3, max_value=60, value=st.session_state.get('max_iterations', 30), step=1, help=t("Maximum number of steps the agent will perform for a single task.", "Ajanın tek bir görevde yapacağı maksimum adım sayısı."))
    st.session_state.max_iterations = max_iterations

    # --- Memory ---
    st.markdown(f'<div class="sidebar-section">{t("Memory", "Hafıza")}</div>', unsafe_allow_html=True)
    mem_count = memory_collection.count()
    st.metric(t("Saved Memory Count", "Kayıtlı Anı Sayısı"), mem_count)
    if mem_count > 0:
        if st.button(t("🧹 Clear Memory", "🧹 Hafızayı Sıfırla"), use_container_width=True):
            chroma_client.delete_collection("research_memory")
            # Recreate immediately so the rest of the app doesn't break
            memory_collection = chroma_client.get_or_create_collection(name="research_memory")
            st.success(t("Memory cleared!", "Hafıza temizlendi!"))
            st.rerun()

    # --- Upload Documents ---
    st.markdown(f'<div class="sidebar-section">{t("Upload Documents", "Doküman Yükle")}</div>', unsafe_allow_html=True)
    
    input_dir = os.path.join(WORKSPACE_DIR, "sandbox", "inputs")
    os.makedirs(input_dir, exist_ok=True)
    
    upload_target = st.radio(
        t("Upload target:", "Yükleme hedefi:"),
        (t("Workspace Data (Active Task)", "Çalışma Alanı (Aktif Görev)"), t("Library (Reference Documents)", "Kütüphane (Referans Belgeler)"))
    )
    
    uploaded_files = st.file_uploader(
        t("Upload files", "Dosyaları yükle"), 
        type=["csv", "pdf", "txt", "docx", "xlsx", "html", "md", "json"],
        accept_multiple_files=True
    )

    if uploaded_files:
        for uploaded_file in uploaded_files:
            target_dir = REFERENCES_DIR if "RAG" in upload_target else input_dir
            file_path = os.path.join(target_dir, uploaded_file.name)
            with open(file_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
        
        st.success(t(f"{len(uploaded_files)} file(s) uploaded!", f"{len(uploaded_files)} dosya yüklendi!"))

    # --- Cleanup ---
    st.markdown(f'<div class="sidebar-section">{t("Cleanup", "Temizlik")}</div>', unsafe_allow_html=True)
    
    cleanup_target = st.radio(
        t("What to clean?", "Neyi temizlemek istersiniz?"),
        (t("Everything", "Her şey"), t("Generated Outputs Only", "Sadece Çıktılar"), t("Uploaded Inputs Only", "Sadece Yüklemeler"))
    )

    if st.button(t("🗑️ Clean Sandbox", "🗑️ Sandbox'ı Temizle"), use_container_width=True):
        import shutil
        import stat
        
        def on_rm_error(func, path, exc_info):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                pass

        sandbox_path = os.path.join(WORKSPACE_DIR, "sandbox")
        outputs_path = os.path.join(sandbox_path, "outputs")
        inputs_path = os.path.join(sandbox_path, "inputs")
        
        cleaned_up = False

        if "Her şey" in cleanup_target or "Everything" in cleanup_target:
            if os.path.exists(sandbox_path):
                shutil.rmtree(sandbox_path, onexc=on_rm_error) if sys.version_info >= (3, 12) else shutil.rmtree(sandbox_path, onerror=on_rm_error)
            os.makedirs(inputs_path, exist_ok=True)
            os.makedirs(outputs_path, exist_ok=True)
            cleaned_up = True
            
        elif "Çıktılar" in cleanup_target or "Outputs" in cleanup_target:
            if os.path.exists(outputs_path):
                shutil.rmtree(outputs_path, onexc=on_rm_error) if sys.version_info >= (3, 12) else shutil.rmtree(outputs_path, onerror=on_rm_error)
            os.makedirs(outputs_path, exist_ok=True)
            cleaned_up = True
            
        elif "Yüklemeler" in cleanup_target or "Inputs" in cleanup_target:
            if os.path.exists(inputs_path):
                shutil.rmtree(inputs_path, onexc=on_rm_error) if sys.version_info >= (3, 12) else shutil.rmtree(inputs_path, onerror=on_rm_error)
            os.makedirs(inputs_path, exist_ok=True)
            cleaned_up = True
            
        if cleaned_up:
            st.success(t(f"Sandbox cleaned: {cleanup_target}", f"Sandbox temizlendi: {cleanup_target}"))
        else:
            st.info(t("Nothing to clean.", "Temizlenecek bir şey yok."))
            
    if st.button(t("🗑️ Clean Old Workspaces", "🗑️ Eski Oturum Dosyalarını Temizle"), use_container_width=True, help=t("Cleans old session folders in agent_workspace", "agent_workspace içindeki eski oturum klasörlerini temizler")):
        import shutil
        import stat
        
        def on_rm_error(func, path, exc_info):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                pass
                
        cleaned = False
        remaining_issues = False
        for item in os.listdir(WORKSPACE_DIR):
            item_path = os.path.join(WORKSPACE_DIR, item)
            if item != "sandbox" and os.path.isdir(item_path):
                try:
                    shutil.rmtree(item_path, onexc=on_rm_error) if sys.version_info >= (3, 12) else shutil.rmtree(item_path, onerror=on_rm_error)
                    cleaned = True
                except Exception:
                    remaining_issues = True
                    
        if remaining_issues:
            st.warning(t("Cleaned some files, but a few remain (they might be in use).", "Bazı dosyalar temizlendi, ancak kullanımda olan bazı dosyalar silinemedi."))
        elif cleaned:
            st.success(t("Old workspaces cleaned!", "Eski oturum dosyaları temizlendi!"))
        else:
            st.info(t("No old workspaces found.", "Eski oturum dosyası bulunamadı."))

    # --- Librarian ---
    st.markdown(f'<div class="sidebar-section">{t("Library (References)", "Kütüphane (Referanslar)")}</div>', unsafe_allow_html=True)
    st.caption(t("Copy PDF/TXT files to the agent_references/ folder.", "PDF/TXT dosyalarını agent_references/ klasörüne kopyalayın."))
    ref_count = references_collection.count()
    st.metric(t("Indexed Chunks Count", "İndekslenen Parça Sayısı"), ref_count)
    if st.button(t("🔄 Index References", "🔄 Referansları İndeksle"), use_container_width=True):
        with st.spinner(t("Indexing files... (This might take a while)", "Dosyalar indeksleniyor... (Bu işlem biraz sürebilir)")):
            try:
                import fitz  # PyMuPDF
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer('all-MiniLM-L6-v2')
            except ImportError:
                st.error(t("Required libraries are missing. Run \'pip install PyMuPDF sentence-transformers\' in the terminal.", "Gerekli kütüphaneler eksik. Terminalde \'pip install PyMuPDF sentence-transformers\' çalıştırın."))
            else:
                try:
                    # Temizleme veya güncelleme yapabiliriz, burada üzerine yazılıyor.
                    for filename in os.listdir(REFERENCES_DIR):
                        filepath = os.path.join(REFERENCES_DIR, filename)
                        text = ""
                        
                        if filename.lower().endswith(".pdf"):
                            doc = fitz.open(filepath)
                            for page in doc:
                                text += page.get_text("text") + "\n"
                        elif filename.lower().endswith(".txt") or filename.lower().endswith(".md"):
                            with open(filepath, "r", encoding="utf-8") as f:
                                text = f.read()
                                
                        if text:
                            # Simple chunking logic (approx 300 words)
                            words = text.split()
                            chunk_size = 300
                            chunks = [" ".join(words[i:i+chunk_size]) for i in range(0, len(words), chunk_size)]
                            
                            for i, chunk in enumerate(chunks):
                                doc_id = f"{filename}_chunk_{i}"
                                embedding = model.encode(chunk).tolist()
                                references_collection.upsert(
                                    documents=[chunk],
                                    embeddings=[embedding],
                                    metadatas=[{"source": filename, "chunk": i}],
                                    ids=[doc_id]
                                )
                    st.success(t("All documents successfully indexed! Rerunning...", "Tüm dokümanlar başarıyla indekslendi! Rerun ediliyor..."))
                    st.rerun()
                except Exception as e:
                    st.error(t(f"Error during indexing: {e}", f"İndeksleme sırasında hata: {e}"))

    # --- Chat Export ---
    st.markdown(f'<div class="sidebar-section">{t("Export", "Dışa Aktar")}</div>', unsafe_allow_html=True)
    if st.session_state.get("chat_history"):
        md_lines = []
        for msg in st.session_state.chat_history:
            role = "🧑 Kullanıcı" if msg["role"] == "user" else "🤖 Asistan"
            badge = msg.get("agent_type", "")
            if badge:
                icon, label = AGENT_LABELS.get(badge, ("🔧", badge))
                role += f" [{icon} {label}]"
            md_lines.append(f"### {role}\n\n{msg['content']}\n")
        export_md = "\n---\n\n".join(md_lines)
        st.download_button(
            t("📥 Download Chat as Markdown", "📥 Sohbeti Markdown Olarak İndir"),
            data=export_md,
            file_name=f"chat_export_{datetime.now():%Y%m%d_%H%M%S}.md",
            mime="text/markdown",
            use_container_width=True,
        )
    else:
        st.info(t("No chat to export yet.", "Henüz dışa aktarılacak sohbet yok."))

    # --- Workspace File Browser ---
    st.markdown(f'<div class="sidebar-section">{t("Workspace", "Çalışma Alanı")}</div>', unsafe_allow_html=True)
    if os.path.isdir(WORKSPACE_DIR):
        ws_items = sorted(os.listdir(WORKSPACE_DIR), reverse=True)
        if ws_items:
            with st.expander(f"📁 agent_workspace ({len(ws_items)} öğe)", expanded=False):
                for item in ws_items[:30]:  # cap to avoid huge lists
                    full = os.path.join(WORKSPACE_DIR, item)
                    if os.path.isdir(full):
                        sub_items = os.listdir(full)
                        st.markdown(f"📂 **{item}/** — {len(sub_items)} " + t("files", "dosya"))
                    else:
                        size_kb = os.path.getsize(full) / 1024
                        st.markdown(f"📄 {item}  `{size_kb:.1f} KB`")
        else:
            st.info(t("Workspace is empty.", "Çalışma alanı boş."))

# ── Session state init ───────────────────────────────────
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# ── Top action row ───────────────────────────────────────
col_clear, _ = st.columns([1, 5])
with col_clear:
    if st.button(t("🗑️ Clear History", "🗑️ Geçmişi Temizle")):
        st.session_state.chat_history = []
        st.rerun()

# ── Render chat history ──────────────────────────────────
for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        # Show agent badge for assistant messages
        if msg["role"] == "assistant" and msg.get("agent_type"):
            agent_key = msg["agent_type"]
            icon, label = AGENT_LABELS.get(agent_key, ("🔧", agent_key))
            css_class = agent_key.split("_")[0].lower()
            st.markdown(f'<span class="agent-badge {css_class}">{icon} {label}</span>', unsafe_allow_html=True)
        st.markdown(msg["content"])
        if msg["role"] == "assistant":
            with st.expander(t("📋 Copy Response / Raw Markdown", "📋 Yanıtı Kopyala / Raw Markdown")):
                st.code(msg["content"], language="markdown")
            
            if "metadata" in msg:
                m = msg["metadata"]
                metrics_html = f"""
                <div style="display: flex; gap: 15px; font-size: 0.8rem; color: #888; margin-top: 10px; padding: 8px 12px; background: rgba(0,0,0,0.1); border-radius: 6px; border: 1px solid rgba(255,255,255,0.05);">
                    <div>⏱️ {m.get('time_str', 'N/A')}</div>
                    <div>🪙 {m.get('total_tokens', 0):,} tokens (In: {m.get('input_tokens', 0):,} | Out: {m.get('output_tokens', 0):,})</div>
                    <div>💵 ${float(m.get('cost', 0)):.4f}</div>
                </div>
                """
                st.markdown(metrics_html, unsafe_allow_html=True)

# ── Chat input ───────────────────────────────────────────
if prompt := st.chat_input(t("What would you like me to do?", "Ne yapmamı istersin?")):
    clear_stop()
    st.session_state.chat_history.append({"role": "user", "content": prompt})
    
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status = st.status(t("Starting Background Agent...", "Arka Plan Aracısı Başlatılıyor..."))
        
        # Working stop button
        stop_placeholder = st.empty()
        stop_placeholder.button(t("🛑 Stop Process", "🛑 İşlemi Durdur"), key=f"stop_{len(st.session_state.chat_history)}", on_click=request_stop)
        
        progress_placeholder = st.empty()
        result_placeholder = st.empty()
        
        try:
            final_answer = asyncio.run(
                run_agent(
                    prompt,
                    status,
                    st.session_state.chat_history,
                    active_client,
                    active_model,
                    result_placeholder=result_placeholder,
                    stop_placeholder=stop_placeholder,
                    progress_placeholder=progress_placeholder,
                    max_iterations=st.session_state.get("max_iterations", 30),
                )
            )
            
            # Tag the just-appended assistant message with agent info
            agent_type = st.session_state.get("_last_agent_type")
            if agent_type and st.session_state.chat_history and st.session_state.chat_history[-1]["role"] == "assistant":
                st.session_state.chat_history[-1]["agent_type"] = agent_type
                
            status.update(label=t("Task Completed!", "Görev Tamamlandı!"), state="complete")
            
        except BaseException as e:
            if type(e).__name__ in ["StopException", "RerunException"]:
                raise e
            
            is_teardown_error = False
            error_msg = str(e).lower()
            error_repr = repr(e).lower()
            
            if len(st.session_state.chat_history) > 0 and st.session_state.chat_history[-1]["role"] == "assistant":
                if "taskgroup" in error_msg or "session" in error_msg or "stream" in error_msg or "taskgroup" in error_repr or "exceptiongroup" in error_repr or "sub-exception" in error_msg:
                    is_teardown_error = True
                    
            if is_teardown_error or (len(st.session_state.chat_history) > 0 and st.session_state.chat_history[-1]["role"] == "assistant"):
                status.update(label=t("Task Completed!", "Görev Tamamlandı!"), state="complete")
                # Tag agent type even on teardown
                agent_type = st.session_state.get("_last_agent_type")
                if agent_type and st.session_state.chat_history[-1]["role"] == "assistant":
                    st.session_state.chat_history[-1]["agent_type"] = agent_type
            else:
                status.update(label=t("Execution Failed", "Çalıştırma Başarısız Oldu"), state="error")
                st.error(t(f"An error occurred: {error_repr}", f"Bir hata oluştu: {error_repr}"))
        finally:
            stop_placeholder.empty()
            progress_placeholder.empty()

# Footer
st.markdown('''
    <style>
    .footer {
        position: fixed;
        left: 0;
        bottom: 0;
        width: 100%;
        background-color: transparent;
        color: grey;
        text-align: center;
        padding: 10px;
        font-size: 12px;
        z-index: 100;
    }
    </style>
    <div class="footer">
        Created by <a href="https://www.emrekizilkaya.com" target="_blank" rel="noopener noreferrer" style="color: inherit; text-decoration: underline;">Emre Kizilkaya</a> in 2026. Open-sourced under the <a href="https://www.mozilla.org/en-US/MPL/2.0/" target="_blank" rel="noopener noreferrer" style="color: inherit; text-decoration: underline;">Mozilla Public License 2.0</a>.
    </div>
''', unsafe_allow_html=True)
