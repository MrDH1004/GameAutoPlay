# 관리자 권한 확인창 없이 브리지/매크로를 실행할 수 있도록 작업 스케줄러에 등록한다.
# 등록할 때 한 번만 관리자 권한이 필요하다.   삭제: .\setup_tasks.ps1 -Remove
param([switch]$Remove)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$user = "$env:USERDOMAIN\$env:USERNAME"
$names = @("GameAutoPlay_Bridge", "GameAutoPlay_Macro")

if ($Remove) {
    foreach ($n in $names) { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue }
    Write-Host "작업 삭제 완료"
    exit
}

$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

# 브리지: 콘솔 창 없이 (pythonw), 로그는 logs\bridge.log
$bridge = New-ScheduledTaskAction -Execute "$root\.venv\Scripts\pythonw.exe" -Argument "-m tools.bridge" -WorkingDirectory $root
Register-ScheduledTask -TaskName $names[0] -Action $bridge -Principal $principal -Settings $settings -Force | Out-Null

# 매크로: 로그를 볼 수 있게 콘솔 창 표시
$macro = New-ScheduledTaskAction -Execute "$root\.venv\Scripts\python.exe" -Argument "main.py" -WorkingDirectory $root
Register-ScheduledTask -TaskName $names[1] -Action $macro -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "등록 완료: $($names -join ', ')"
