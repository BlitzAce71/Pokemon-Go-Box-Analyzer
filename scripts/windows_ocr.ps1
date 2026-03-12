param(
    [Parameter(Mandatory = $true)]
    [string]$ImagePath
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Runtime.WindowsRuntime

[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Media.Ocr.OcrLine, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Media.Ocr.OcrWord, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Storage.FileAccessMode, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Storage.Streams.IRandomAccessStream, Windows.Foundation, ContentType = WindowsRuntime]

function Invoke-AsTaskGeneric {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Operation,
        [Parameter(Mandatory = $true)]
        [Type]$ResultType
    )

    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq 'AsTask' -and
            $_.IsGenericMethodDefinition -and
            $_.GetGenericArguments().Count -eq 1 -and
            $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -like 'IAsyncOperation*'
        } |
        Select-Object -First 1

    $genericMethod = $method.MakeGenericMethod($ResultType)
    return $genericMethod.Invoke($null, @($Operation))
}

$fullPath = (Resolve-Path $ImagePath).Path

$file = (Invoke-AsTaskGeneric -Operation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($fullPath)) -ResultType ([Windows.Storage.StorageFile])).GetAwaiter().GetResult()
$stream = (Invoke-AsTaskGeneric -Operation ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) -ResultType ([Windows.Storage.Streams.IRandomAccessStream])).GetAwaiter().GetResult()
$decoder = (Invoke-AsTaskGeneric -Operation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) -ResultType ([Windows.Graphics.Imaging.BitmapDecoder])).GetAwaiter().GetResult()
$bitmap = (Invoke-AsTaskGeneric -Operation ($decoder.GetSoftwareBitmapAsync()) -ResultType ([Windows.Graphics.Imaging.SoftwareBitmap])).GetAwaiter().GetResult()

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {
    throw "Failed to create OcrEngine from user profile languages."
}

$result = (Invoke-AsTaskGeneric -Operation ($engine.RecognizeAsync($bitmap)) -ResultType ([Windows.Media.Ocr.OcrResult])).GetAwaiter().GetResult()

$lines = @()
foreach ($line in $result.Lines) {
    $minX = [double]::PositiveInfinity
    $minY = [double]::PositiveInfinity
    $maxX = [double]::NegativeInfinity
    $maxY = [double]::NegativeInfinity

    foreach ($word in $line.Words) {
        $r = $word.BoundingRect
        if ($r.Width -le 0 -or $r.Height -le 0) {
            continue
        }
        $left = [double]$r.X
        $top = [double]$r.Y
        $right = $left + [double]$r.Width
        $bottom = $top + [double]$r.Height

        if ($left -lt $minX) { $minX = $left }
        if ($top -lt $minY) { $minY = $top }
        if ($right -gt $maxX) { $maxX = $right }
        if ($bottom -gt $maxY) { $maxY = $bottom }
    }

    if ([double]::IsInfinity($minX) -or [double]::IsInfinity($minY)) {
        $x = 0
        $y = 0
        $w = 0
        $h = 0
    }
    else {
        $x = [int][Math]::Round($minX)
        $y = [int][Math]::Round($minY)
        $w = [int][Math]::Round([Math]::Max(0.0, $maxX - $minX))
        $h = [int][Math]::Round([Math]::Max(0.0, $maxY - $minY))
    }

    $lines += [PSCustomObject]@{
        text = $line.Text
        x = $x
        y = $y
        w = $w
        h = $h
    }
}

$output = [PSCustomObject]@{
    text = $result.Text
    lines = $lines
}

$output | ConvertTo-Json -Depth 4 -Compress
