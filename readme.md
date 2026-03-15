# IntelAgent: A Privacy-Centric Autonomous Agent for Researchers and Journalists

IntelAgent is a locally executed, privacy-first artificial intelligence assistant engineered for complex, multi-step tasks. Designed specifically for investigative journalists and scientific researchers, the architecture prioritizes data sovereignty and operational security. To support professionals in critical environments, the application features an interface available in English, as well as the native languages of two fragile democracies where journalists and researchers face immense pressure: Turkey and Hungary. By utilizing local large language models (LLMs) and open-weights alternatives, the system mitigates the privacy risks associated with proprietary, cloud-based application programming interfaces (APIs). Furthermore, this app democratizes access to programming; the user can use various Python functions by simply chatting with the bot.

## Initialization and Configuration

### 1. Install Dependencies
Before running the application, make sure to install the required Python packages. Execute the following command in your terminal:

`ash
pip install -r requirements.txt
`

### 2. Environment Variables (.env)
Users must configure a .env file in the root directory. To do this, duplicate the .env.example file and rename it to .env.
Set the PROJECTS_DIR variable to define the agent's primary operational environment. If omitted, the system defaults to the application's parent directory.

### 3. Run the Application

To initiate the agent, execute the following command in your terminal:

```bash
streamlit run app.py
```

The repository includes a batch (.bat) file for automated startup. Upon initialization, the user interface prompts the selection of either a local LM Studio model or a model on OpenRouter, an LLM platform.

**Repository Configuration Note:**
The `agent_memory`, `agent_workspace`, and `agent_references` directories are excluded from version control to maintain a lightweight repository. The application generates these directories automatically during the initial execution.

Users must configure a `.env` file in the root directory. Set the `PROJECTS_DIR` variable to define the agent's primary operational environment. If omitted, the system defaults to the application's parent directory.

```env
# Local Model Configuration
LOCAL_MODEL_NAME=mistralai/ministral-3-14b-reasoning

# System Directories and API Keys
PROJECTS_DIR=C:\path\to\your\projects
OPENROUTER_KEY=your_openrouter_api_key
BRAVE_API_KEY=your_brave_search_key
S2_API_KEY=your_semantic_scholar_api_key
GITHUB_PERSONAL_ACCESS_TOKEN=your_github_token

# Optional: Corporate Integrations
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
```

## Model Selection and Data Sovereignty

The system architecture encourages the use of local LLMs, which is critical when processing sensitive journalistic or research data. IntelAgent serves as a secure alternative to popular agentic assistants such as OpenClaw, which presents documented security vulnerabilities acknowledged even by its own development team. The default local configuration utilizes the `mistralai/ministral-3-14b-reasoning` open-weights model via an LM Studio server, offering high capability on mid-tier hardware. Executing it locally ensures that all data processing remains strictly confined to the user's hardware by default, eliminating unauthorized external data transmission. Users can substitute this with any compatible local model.

For tasks requiring greater computational power without local hardware constraints, the system supports OpenRouter, a platform that enables to use any LLM through an API. The default OpenRouter selection in the app is Mistral Large, an Apache-2.0 licensed, open-weights architecture developed in Europe. This provides a robust alternative to foundational models originating from the United States and China, aligning with the project's emphasis on open-source technology.

## Corporate Integrations (Google Workspace)

While the developer strongly advocates for open-source software to ensure maximum privacy, IntelAgent permits integration with corporate services like the U.S.-based Google Workspace (Docs, Sheets, Drive, Calendar) for users who require them. To prevent data leakage, the repository's `.gitignore` file strictly excludes authentication tokens (`token.json`, `credentials.json`). 

To configure these optional integrations:

1. Access the Google Cloud Console.
2. Generate a new project and enable the necessary APIs.
3. Establish an OAuth Client ID configured as a Desktop or Web application.
4. Input the `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` into the `.env` file.

The initial execution of a Google-dependent skill triggers a secure, localized OAuth authentication process, generating an untracked token file within the respective skill directory.

## System Architecture

The application operates on a dynamic, modular architecture. An orchestration engine evaluates user prompts and routes tasks to specialized sub-agents. 



```text
+---------------------------------------------------------------------------------+
|                                Streamlit UI (app.py)                            |
| [ Model Selection ]  [ Memory Controls ]  [ Chat Interface ]  [ Stop Execution ]|
+----------------------------------+----------------------------------------------+
                                   |
                                   v
+---------------------------------------------------------------------------------+
|                            Orchestration Engine                                 |
| 1. Evaluates Request (via Manager LLM: Local LM Studio or OpenRouter)           |
| 2. Retrieves Context (from Chroma DB Agent Memory)                              |
| 3. Selects Next Best Sub-Agent/Skill                                            |
+---+------------------------------+------------------------------+---------------+
    |                              |                              |
    v                              v                              v
+---+---------------+      +-------+---------+        +-----------+-----------+
|   Memory & RAG    |      |  MCP Skills Hub |        |   Agent Workspace     |
| (agent_memory/ &  |      |   (skills/*)    |        |  (agent_workspace/)   |
| agent_references/)|      +-------+---------+        +-+--+------+---------+-+
+---+---------------+              |                    |  |      |         |
    |                              |                    |  |      |         |
    |   +--------------------------+--------------------+  |      |         |
    v   v                                                  v      v         v
+---+---------------+   +----------+---------+   +---------+------+-------+-+---------+
| ChromaDB Store    |   | Web & Research     |   | Data & Documents       | Scrapers  |
| - Task Histories  |   | - WEB_SEARCHER     |   | - DATA_ANALYST         | - Puppe-  |
| - Prior Context   |   | - DEEP_RESEARCH    |   | - DOCUMENT_SPECIALIST  |    teer   |
|                   |   | - ACADEMIC_SEARCHER|   |                        | - BS4     |
| Local RAG Files   |   |                    |   +------------------------+           |
| (PDFs/Txts for    |   |                    |   | Integrations & System  |           |
|  LIBRARIAN RAG)   |   |                    |   | - GITHUB_MANAGER       |           |
|                   |   |                    |   | - ENV_MANAGER          |           |
|                   |   |                    |   | - GOOGLE (Docs/Sheets) |           |
+-------------------+   +--------------------+   +------------------------+-----------+
                                   |
                                   v
+---------------------------------------------------------------------------------+
|                        Execution & Output Generation                            |
| - Runs the chosen tool calls securely inside the sandboxed workspace.           |
| - Final answer & execution trace are streamed to UI and committed to memory.    |
+---------------------------------------------------------------------------------+
```

### Integrated Skills

The system includes 14 default skills loaded dynamically from the `skills/` directory.

| Skill | Primary Function | Core Technology / Methodology |
| :--- | :--- | :--- |
| **Academic Searcher** | Synthesizes academic literature with APA citations. | Semantic Scholar API |
| **Advanced Web Scraper** | Extracts structured datasets and dynamic HTML. | Puppeteer, BeautifulSoup |
| **Data Analyst** | Executes statistical analyses and generates visualizations. | Python (Pandas, Seaborn) |
| **Deep Research** | Conducts multi-step autonomous investigations. | Brave Search, Semantic Scholar |
| **Document Specialist** | Formats and manipulates office documents and PDFs. | Python Document Libraries |
| **Environment Manager** | Handles `.env` variables securely. | Local File System |
| **FoI Request Writer** | Drafts more effective Freedom of Information requests by utilizing best practices from the International Press Institute (IPI). | Legal text synthesis |
| **GitHub Manager** | Oversees repository workflows and pull requests, if the user is on GitHub. | GitHub MCP |
| **Google Workspace Manager** | Integrates Drive, Docs, Sheets, and Calendar tasks. | Google APIs (Optional) |
| **Librarian** | Queries indexed reference documents locally with semantic search. | `sentence-transformers` |
| **Local File Manager** | Manages local file system operations, such as creating or moving files. | OS Libraries |
| **News Discovery** | Retrieves and analyzes recent news from curated RSS feeds. | RSS / XML Parsing |
| **Newsroom Editor** | Copyedits journalistic texts according to configurable newsroom guidelines, defaulting to AP style. | Text Processing |
| **Web Searcher** | Executes general internet queries. | Brave Search API |

### Extending Capabilities

Users can engineer new sub-agents without modifying the core orchestration code (`app.py`). To integrate a new skill:

1. Construct a dedicated directory within the `skills/` folder.
2. Formulate a `manifest.json` file defining the metadata, routing description, and Model Context Protocol (MCP) server arguments.
3. Draft an `instructions.md` file establishing the strict parameters and rules for the language model utilizing the skill.
4. Restart the application. The orchestrator will detect the new directory and integrate it into the routing logic.

### Settings & Data Management

The sidebar in IntelAgent's UI provides several tools to manage the agent's memory, files, and reference data. While some of these "cleaning" actions may seem similar, they serve distinctly different purposes in managing the agent's local environment:

*   **Clear Memory:** IntelAgent continuously learns by saving the reasoning and output of every successful task you give it into a local vector database (Chroma). This allows the agent to recall past context in future conversations automatically. Clicking "*Clear Memory*" resets this database, wiping the agent's contextual history of your past tasks without disabling its core capabilities.
*   **Clean Sandbox:** The agent performs its active work in a temporary "sandbox" folder (`agent_workspace/sandbox/inputs` and `outputs`). This button lets you selectively delete the files you uploaded (Inputs) or the files the agent generated (Outputs) for your current task. This is useful for resetting the agent's immediate working directory before starting a new data analysis or document formatting job.
*   **Clean Old Workspaces:** Every time you run a task, the agent leaves behind an execution log within a timestamped backup folder (e.g., `agent_workspace/2026-03-12.../`). Over time, these archived logs can take up disk space. Clicking "*Clean Old Workspaces*" deletes all historical timestamped workspace folders to clear up storage, leaving your current sandbox and memory intact.
*   **Index References:** If you upload documents (PDFs, TXTs, or Markdown) to the "Library (Reference Documents)" target in the UI, they go into the `agent_references/` folder. Clicking "*Index References*" parses these files and generates deep local embeddings (`sentence-transformers`), allowing the "Librarian" sub-agent to query your private documents securely via Retrieval-Augmented Generation (RAG) without relying on an external search engine.

## License

This project is open-sourced under the Mozilla Public License 2.0.
