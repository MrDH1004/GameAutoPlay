$ErrorActionPreference = 'Stop'
$codexCommand = Join-Path $env:APPDATA 'npm\codex.cmd'
if (-not (Test-Path -LiteralPath $codexCommand)) { throw 'Codex CLI was not found.' }
$label = ([char[]](0xC5EC,0xAE30,0xC11C) -join '') + ' Codex ' + ([char[]](0xC2E4,0xD589) -join '')
$entries = @(
    @{ Path = 'Software\Classes\Directory\shell\CodexHere'; Target = '%1' },
    @{ Path = 'Software\Classes\Directory\Background\shell\CodexHere'; Target = '%V' },
    @{ Path = 'Software\Classes\Drive\shell\CodexHere'; Target = '%1' }
)
foreach ($entry in $entries) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($entry.Path)
    try {
        $key.SetValue('', $label)
        $key.SetValue('Icon', "$env:SystemRoot\System32\cmd.exe,0")
        $key.SetValue('MultiSelectModel', 'Single')
        $command = $key.CreateSubKey('command')
        try {
            $value = '"' + $env:SystemRoot + '\System32\cmd.exe" /d /k ""' + $codexCommand + '" -C "' + $entry.Target + '""'
            $command.SetValue('', $value)
        } finally { $command.Dispose() }
    } finally { $key.Dispose() }
}
foreach ($entry in $entries) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($entry.Path + '\command')
    try { Write-Output ($entry.Path + ': ' + $key.GetValue('')) } finally { $key.Dispose() }
}
