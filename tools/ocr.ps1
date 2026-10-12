param([string]$Path, [string]$Lang = "ko")
# Windows 내장 OCR로 이미지의 글자를 읽어 JSON 배열로 출력: [{text, x, y, w, h}] (줄 단위, 이미지 픽셀 좌표)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
[void][Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    [void]$task.Wait(-1)
    $task.Result
}

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($Lang))
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

$out = @()
foreach ($line in $result.Lines) {
    $xs = @(); $ys = @(); $xe = @(); $ye = @()
    foreach ($w in $line.Words) {
        $r = $w.BoundingRect
        $xs += $r.X; $ys += $r.Y; $xe += ($r.X + $r.Width); $ye += ($r.Y + $r.Height)
    }
    if ($xs.Count -eq 0) { continue }
    $x = ($xs | Measure-Object -Minimum).Minimum; $y = ($ys | Measure-Object -Minimum).Minimum
    $out += [pscustomobject]@{
        text = $line.Text; x = [int]$x; y = [int]$y
        w = [int](($xe | Measure-Object -Maximum).Maximum - $x); h = [int](($ye | Measure-Object -Maximum).Maximum - $y)
    }
}
ConvertTo-Json -InputObject @($out) -Compress
