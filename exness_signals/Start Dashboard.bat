@echo off
cd /d "%~dp0"
echo Starting Exness Signal Bot dashboard... (close this window to stop)
python -m streamlit run app.py --server.port 8501
