# scripts/uninstall_autostart.ps1
# 移除 OmniContext 開機自動啟動工作排程

$TaskName = "OmniContext_ActivityTracker"

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "✅ 已成功移除 OmniContext 工作排程 ($TaskName)。" -ForegroundColor Green
