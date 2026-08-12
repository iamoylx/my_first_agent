' Xiaoman WeChat bridge - hidden background launcher (wscript, window style 0 = fully hidden)
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
sh.Run "cmd /c node index.js start >> ..\logs\wechat-bridge.out.log 2>> ..\logs\wechat-bridge.err.log", 0, False
