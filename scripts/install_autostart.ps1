# scripts/install_autostart.ps1
# 使用 Windows 工作排程器 (Task Scheduler) 設定登入時自動以 pythonw.exe 背景啟動 OmniContext

$TaskName = "OmniContext_ActivityTracker"
$ProjectPath = "D:\Project_CodingSimulation\PersonalHelper\activityTracker"
$PythonwExe = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source

if (-not $PythonwExe) {
    # 尋找 python.exe 旁的 pythonw.exe
    $PythonExe = (Get-Command python.exe).Source
    $PythonwExe = Join-Path (Split-Path $PythonExe) "pythonw.exe"
}

if (-not (Test-Path $PythonwExe)) {
    Write-Error "找不到 pythonw.exe，請確認 Python 已加入 PATH。"
    exit 1
}

$Action = New-ScheduledTaskAction -Execute $PythonwExe -Argument "main.py run" -WorkingDirectory $ProjectPath
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 365)

# 註冊工作排程
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force

Write-Host "✅ 成功安裝 OmniContext 開機自動登入背景啟動工作排程！" -ForegroundColor Green
Write-Host "工作名稱: $TaskName"
Write-Host "執行程式: $PythonwExe main.py run"
Write-Host "工作目錄: $ProjectPath"
