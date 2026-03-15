import os
from mcp.server.fastmcp import FastMCP
import chromadb
from sentence_transformers import SentenceTransformer

# System Configuration
MEMORY_DIR = os.path.abspath("./agent_memory")

# Initialize ChromaDB and Embeddings
chroma_client = chromadb.PersistentClient(path=MEMORY_DIR)
references_collection = chroma_client.get_or_create_collection(name="references")

# Load embedding model globally to reuse
embedding_model = SentenceTransformer('all-MiniLM-L6-v2')

# Create the MCP Server
mcp = FastMCP("LIBRARIAN", dependencies=["chromadb", "sentence-transformers"])

@mcp.tool()
def search_references(query: str, n_results: int = 4) -> str:
    """
    Search the local reference documents for the given query using embeddings.
    Always use this to answer questions about the user's uploaded PDFs and text files.
    """
    if references_collection.count() == 0:
        return "No documents indexed. Tell the user to index Reference documents first via the UI."
    
    try:
        query_embedding = embedding_model.encode(query).tolist()
        results = references_collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results
        )
        
        if not results['documents'] or not results['documents'][0]:
            return "No relevant information found in the reference documents."
            
        context = []
        for i, doc in enumerate(results['documents'][0]):
            meta = results['metadatas'][0][i]
            source = meta.get("source", "Unknown")
            context.append(f"--- Document Source: {source} ---\n{doc}\n")
            
        final_result = f"Found {len(context)} relevant sections from the references:\n\n"
        final_result += "\n".join(context)
        return final_result
    
    except Exception as e:
        return f"Error occurred while searching references: {e}"

if __name__ == "__main__":
    mcp.run(transport='stdio')
