@echo off
TITLE Local AI Agent Baslatici

:: 1. Calisma Dizinine Git ve VS Code'u Gorev Cubuguna Kucultulmus Olarak Ac
cd /d "%~dp0"
start "" /min code .

:: 2. Sanal Ortami Aktive Et ve Streamlit'i Calistir
echo Uygulama tarayicida aciliyor...
call .\.venv\Scripts\activate.bat
streamlit run app.py