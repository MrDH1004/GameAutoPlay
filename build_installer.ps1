$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    throw 'Python 개발 환경이 없습니다.'
}
& '.\.venv\Scripts\python.exe' -m tools.build_installer
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
