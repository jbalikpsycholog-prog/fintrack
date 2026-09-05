' Spusteni FinTracku bez jakehokoli viditelneho okna (zadne cerne "cmd" okno).
' Po par vterinach se sam otevre prohlizec a v system tray (dole u hodin)
' se objevi maly modry kolecko - ikona FinTracku. Pres ni jde aplikace
' kdykoliv znovu otevrit, nebo ukoncit (klik pravym tlacitkem mysi).
'
' Pokud jeste nemas pripravene prostredi (poprve po stazeni aplikace),
' spust nejdriv jednou setup.bat - ten vse pripravi a FinTrack rovnou spusti.

Dim objShell, objFSO, scriptDir, pythonwPath, trayScript

Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

scriptDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
pythonwPath = scriptDir & "\.venv\Scripts\pythonw.exe"
trayScript = scriptDir & "\fintrack_tray.pyw"

If Not objFSO.FileExists(pythonwPath) Then
    MsgBox "Prostredi FinTracku jeste neni pripravene." & vbCrLf & vbCrLf & _
           "Spust prosim nejdriv jednou soubor setup.bat (dvojklikem) - " & _
           "pripravi vse potrebne a FinTrack rovnou spusti.", _
           vbExclamation, "FinTrack"
    WScript.Quit
End If

objShell.CurrentDirectory = scriptDir
objShell.Run """" & pythonwPath & """ """ & trayScript & """", 0, False
