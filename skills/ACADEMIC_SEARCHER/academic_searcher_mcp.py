import httpx
import os
import time
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

# Initialize FastMCP server
mcp = FastMCP("AcademicSearcher")

S2_API_KEY = os.environ.get("S2_API_KEY", "") # Setting to blank to bypass 403. An API key is optional for 1 RPS limit.
S2_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

@mcp.tool()
def search_academic_papers(query: str, limit: int = 5) -> str:
    """
    Search for academic papers using Semantic Scholar API given a scientific query.
    Returns paper details like title, authors, year, abstract, venue, and URL to be used in analysis and APA citation generation.
    NOTE: The maximum returned papers is limited by 'limit' parameter (default 5).
    """
    # If an API key is provided, use it. Otherwise, rely on the public endpoint.
    headers = {}
    if S2_API_KEY and S2_API_KEY.strip():
        headers["x-api-key"] = S2_API_KEY
    
    params = {
        "query": query,
        "limit": limit,
        "fields": "title,authors,year,abstract,venue,url,journal"
    }

    try:
        # Retry mechanism for 429 Too Many Requests
        max_retries = 3
        for attempt in range(max_retries):
            # Rate limit safety: wait between requests
            time.sleep(3.0 * (attempt + 1))
            with httpx.Client() as client:
                response = client.get(S2_URL, headers=headers, params=params, timeout=15.0)
                
                if response.status_code == 429 and attempt < max_retries - 1:
                    continue  # Retry
                    
                response.raise_for_status()
                data = response.json()
                break
        
        if "data" not in data or not data["data"]:
            return "No academic papers found for the given query."

        results = []
        for i, paper in enumerate(data["data"]):
            title = paper.get("title", "No Title")
            year = paper.get("year", "n.d.")
            abstract = paper.get("abstract", "No abstract available.")
            venue = paper.get("venue", "")
            journal = paper.get("journal", {})
            if journal and "name" in journal:
                venue = journal.get("name")
            
            url = paper.get("url", "No URL")
            
            authors_list = paper.get("authors", [])
            authors_str = ", ".join([a.get("name", "") for a in authors_list]) if authors_list else "Unknown Author"

            paper_summary = (
                f"Paper {i+1}:\n"
                f"Title: {title}\n"
                f"Authors: {authors_str}\n"
                f"Year: {year}\n"
                f"Venue/Journal: {venue}\n"
                f"URL: {url}\n"
                f"Abstract: {abstract}\n"
            )
            results.append(paper_summary)

        return "\n" + "-"*40 + "\n".join(results)
    
    except Exception as e:
        return f"Error in search_academic_papers: {str(e)}"

if __name__ == "__main__":
    mcp.run()
