Dim fso
Dim shell
Dim sourceFolder
Dim backupFolder
Dim zipFile
Dim dateStamp

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("Shell.Application")

sourceFolder = "C:\ATS_SuperEngine"
backupFolder = "D:\ats_superengine_2026"

If Not fso.FolderExists(backupFolder) Then
    fso.CreateFolder(backupFolder)
End If

dateStamp = Year(Now) & "-" & Right("0" & Month(Now),2) & "-" & Right("0" & Day(Now),2)

zipFile = backupFolder & "\ATS_Backup_" & dateStamp & ".zip"

'create empty zip
Dim zip
Set zip = fso.CreateTextFile(zipFile, True)
zip.Write "PK" & Chr(5) & Chr(6) & String(18, Chr(0))
zip.Close

WScript.Sleep 1000

shell.NameSpace(zipFile).CopyHere shell.NameSpace(sourceFolder).Items

WScript.Sleep 3000

MsgBox "Backup ATS selesai!" & vbCrLf & zipFile