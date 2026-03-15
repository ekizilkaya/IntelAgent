import json
import requests
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("WEB_SCRAPER_SERVICE")

@mcp.tool()
def extract_tables_from_url(url: str) -> str:
    """
    Fetches a URL and extracts all HTML tables into a structured JSON string.
    Use this to quickly harvest tabular datasets from static webpages.
    
    Args:
        url: The absolute URL to scrape (must include http:// or https://)
    """
    try:
        import pandas as pd
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # Use pandas to read tables directly
        tables = pd.read_html(response.text)
        if not tables:
            return json.dumps({"status": "error", "message": "No tables found on the target page."})
        
        result = []
        for i, df in enumerate(tables):
            # Clean up NaN values for clean JSON serialization
            df = df.fillna("")
            result.append({
                "table_index": i,
                "columns": list(df.columns),
                "row_count": len(df),
                "data": df.to_dict(orient="records")
            })
            
        return json.dumps(result, ensure_ascii=False)
    except ImportError:
        return "Error: pandas or lxml missing. Please install pandas and lxml."
    except Exception as e:
        return f"Error scraping tables: {e}"

@mcp.tool()
def extract_structured_content(url: str, css_selector: str = None) -> str:
    """
    Fetches a URL and extracts text content into a clean string format. 
    If a CSS selector is provided, it only extracts elements matching the selector.
    
    Args:
        url: The absolute URL to scrape.
        css_selector: (Optional) A valid CSS selector (e.g., '.product-title', 'article > p') to target specific data.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Remove noisy tags
        for el in soup(["script", "style", "nav", "footer", "meta", "noscript"]):
            el.decompose()
            
        if css_selector:
            elements = soup.select(css_selector)
            if not elements:
                return f"No elements found matching selector: {css_selector}"
            return "\n---\n".join([el.get_text(separator=" ", strip=True) for el in elements])
        else:
            return soup.get_text(separator="\n", strip=True)
            
    except Exception as e:
        return f"Error extracting content: {e}"

if __name__ == "__main__":
    mcp.run()
