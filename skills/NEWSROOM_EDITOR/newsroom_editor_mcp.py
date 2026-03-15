import os
import sys
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("NEWSROOM_EDITOR")

# Klasor yolu (scriptin oldugu klasorden resources icine)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCES_DIR = os.path.join(BASE_DIR, "resources")

@mcp.tool()
def get_style_guide() -> str:
    """
    Haber merkezinin yazim ve stil rehberini (style guide) okur
    ve metin duzenlemelerinde redaktorun dikkate almasi gereken kurallari dondurur.
    Her redaksiyon (copyediting) isleminden once veya kurallari hatirlamak icin cagirin.
    Eger ozel bir stil rehberi yoksa varsayilan olarak AP Stylebook ozetini dondurur.
    """
    guide_path = os.path.join(RESOURCES_DIR, "haber_merkezi_stil_rehberi.md")
    
    if not os.path.exists(guide_path):
        return """Stil rehberi bulunamadi. Varsayilan AP Stylebook Ozet Kurallari Gecerlidir:
        
1. Sayilar: 1-9 arasi sayilari yaziyla (bir, dokuz), 10 ve sonrasini rakamla (10, 15) yazin. Yaslar ve saatler daima rakamdir.
2. Tarihler ve Aylar: Spesifik bir tarihle birlikte kullaniliyorsa uzun aylari (Jan., Feb., Aug., Sept., vs.) kisaltin. Mart, Nisan, Mayis, Haziran, Temmuz her zaman tam yazilir.
3. Unvanlar: Kisilerin adlarindan once gelen resmi unvanlari buyuk harfle baslatin (Orn: Baskan Ahmet). Ilk kullanimda ad-soyad, sonraki kullanimlarda sadece soyad kullanin.
4. Virgeller (Oxford Comma): Basit bir liste veya seri icindeki 've' veya 'veya' kelimesinden once virgul KULLANMAYIN (Orn: kirmizi, mavi ve sari).
5. Kısaltmalar: Ilk gectiginde acik ismini yazin. Sadece cok bilinen (FBI, NASA) kisaltmalari ilk kullanimda dogrudan kullanabilirsiniz.
6. Nesnellik: Yorum veya kisisel gorus iceren sifar ve zarflari kaldirin. Sadece gercekleri aktarin. Ters piramit kuralina uyun."""
        
    try:
        with open(guide_path, "r", encoding="utf-8") as file:
            content = file.read()
        return f"Haber Merkezi Stil Rehberi Kural ve Prensipleri:\n\n{content}"
    except Exception as e:
        return f"Stil rehberi okunurken hata olustu: {str(e)}"

# Opsiyonel: Baska faydali redaktor araclari eklenebilir, ornegin kelime/karakter sayma
@mcp.tool()
def count_words(text: str) -> dict:
    """
    Metindeki kelime ve karakter sayilarini analiz eder.
    Haber metinlerinde karakter sinirlamalari veya baslik uzunlugu kontrolleri icin kullanilabilir.
    """
    words = text.split()
    word_count = len(words)
    char_count = len(text)
    char_count_no_spaces = len(text.replace(" ", "").replace("\n", ""))
    
    return {
        "word_count": word_count,
        "char_count": char_count,
        "char_count_no_spaces": char_count_no_spaces
    }

if __name__ == "__main__":
    mcp.run()
