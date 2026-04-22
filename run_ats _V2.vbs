Set WshShell = CreateObject("WScript.Shell")

WshShell.Run "cmd /c cd /d C:\ATS_SuperEngine && streamlit run dashboard_v2.py", 0

Set WshShell = Nothing