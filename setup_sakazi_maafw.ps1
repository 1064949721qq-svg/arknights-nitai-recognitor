param(
    [string]$ProjectDir = "",
    [string]$OcrAssetBase = "https://raw.githubusercontent.com/MaaXYZ/MaaCommonAssets/main/OCR/ppocr_v5/zh_cn"
)

$ErrorActionPreference = "Stop"

if (-not $ProjectDir) {
    $ProjectDir = $PSScriptRoot
}

function Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Download-IfMissing([string]$Url, [string]$OutFile) {
    if (Test-Path -LiteralPath $OutFile) {
        Write-Host "exists: $OutFile"
        return
    }
    Write-Host "download: $Url"
    Invoke-WebRequest -Uri $Url -OutFile $OutFile
}

if (-not (Test-Path -LiteralPath $ProjectDir)) {
    throw "Project directory not found: $ProjectDir"
}

Step "Enter project directory"
Set-Location -LiteralPath $ProjectDir

Step "Create Python virtual environment"
if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    python -m venv .venv
}
$PythonExe = ".\.venv\Scripts\python.exe"

Step "Install MaaFramework Python package"
& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r requirements.txt

Step "Download OCR model assets"
$OcrDir = Join-Path $ProjectDir "resource\model\ocr"
New-Item -ItemType Directory -Force -Path $OcrDir | Out-Null
Download-IfMissing "$OcrAssetBase/det.onnx" (Join-Path $OcrDir "det.onnx")
Download-IfMissing "$OcrAssetBase/rec.onnx" (Join-Path $OcrDir "rec.onnx")
Download-IfMissing "$OcrAssetBase/keys.txt" (Join-Path $OcrDir "keys.txt")

Step "Create local config.json"
if (-not (Test-Path -LiteralPath "config.json")) {
    Copy-Item -LiteralPath "config.example.json" -Destination "config.json"
    Write-Host "created: config.json"
} else {
    Write-Host "exists: config.json"
}

Step "Download Android platform-tools"
$ToolsDir = Join-Path $ProjectDir "tools"
$PlatformDir = Join-Path $ToolsDir "platform-tools"
$AdbExe = Join-Path $PlatformDir "adb.exe"
$PlatformZip = Join-Path $ToolsDir "platform-tools-latest-windows.zip"
New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
if (-not (Test-Path -LiteralPath $AdbExe)) {
    Invoke-WebRequest -Uri "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" -OutFile $PlatformZip
    Expand-Archive -LiteralPath $PlatformZip -DestinationPath $ToolsDir -Force
}

Step "Configure local adb path"
$ConfigPath = Join-Path $ProjectDir "config.json"
$Config = Get-Content -Raw -Encoding UTF8 -LiteralPath $ConfigPath | ConvertFrom-Json
$Config.adb.adb_path = "tools/platform-tools/adb.exe"
$Config | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 -LiteralPath $ConfigPath

Step "Patch default config selection"
$MainPy = Join-Path $ProjectDir "sakazi_shop_detector.py"
$MainText = Get-Content -Raw -Encoding UTF8 -LiteralPath $MainPy
$Pattern = '(?m)^    parser\.add_argument\("--config", default=str\(ROOT / "config\.example\.json"\), help="[^"]*"\)$'
$Replacement = @'
    default_config = ROOT / "config.json"
    if not default_config.exists():
        default_config = ROOT / "config.example.json"
    parser.add_argument("--config", default=str(default_config), help="config file path")
'@.TrimEnd()
$PatchedText = [regex]::Replace($MainText, $Pattern, $Replacement, 1)
if ($PatchedText -ne $MainText) {
    Set-Content -Encoding UTF8 -LiteralPath $MainPy -Value $PatchedText
    Write-Host "patched: sakazi_shop_detector.py"
} else {
    Write-Host "skip patch: default config line not found or already patched"
}

Step "Create run.ps1"
$RunScript = @'
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
& ".\.venv\Scripts\python.exe" ".\sakazi_shop_detector.py" --config ".\config.json" @args
'@
Set-Content -Encoding UTF8 -LiteralPath (Join-Path $ProjectDir "run.ps1") -Value $RunScript

Step "Verify maafw import"
& $PythonExe -c "import maa; print('maafw import ok:', maa.__file__)"

Step "Verify OCR files"
foreach ($Name in @("det.onnx", "rec.onnx", "keys.txt")) {
    $Path = Join-Path $OcrDir $Name
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing OCR asset: $Path"
    }
    Write-Host "ok: $Path"
}

Step "Done"
Write-Host "Run GUI:"
Write-Host "  cd `"$ProjectDir`""
Write-Host "  .\run.ps1"
Write-Host ""
Write-Host "Run once:"
Write-Host "  .\run.ps1 --once"
