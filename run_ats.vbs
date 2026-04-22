Set WshShell = CreateObject("WScript.Shell")

' ganti path sesuai folder ATS SuperEngine
WshShell.CurrentDirectory = "C:\ATS_SuperEngine"

WshShell.Run "cmd /c streamlit run dashboard.py", 0

Set WshShell = Nothing