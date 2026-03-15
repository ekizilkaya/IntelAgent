import os
import sys
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("FOI_REQUEST_WRITER")

@mcp.tool()
def analyze_request_quality(request_text: str) -> str:
    """
    Kullanicinin girmek istedigi Bilgi Edinme basvurusunun yeterince spesifik olup
    olmadigini basit kurallarla kontrol eder (numaralar, spesifik kelimeler vb).
    """
    missing_elements = []
    
    if "kac" not in request_text.lower() and "?" not in request_text:
        missing_elements.append("Basvuru cok genel gorunuyor, miktar veya sayisal veri iceren spesifik ('kac', 'hangi oranda', 'toplam butce') kelimeler eksik olabilir.")
    
    if len(request_text.split()) < 10:
        missing_elements.append("Talebini cok kisa anlattin, daha fazla detay vermelisin.")
        
    if not missing_elements:
        return "Talebin yeterince detayli gorunuyor. Basvuru metnini hukuki emsallerle birlikte cikarabiliriz."
    else:
        return "Talebin asagidaki konularda eksik: " + " ".join(missing_elements)

if __name__ == "__main__":
    mcp.run()
