param([switch]$Clean)

$ErrorActionPreference = "Stop"
$projectDir = $PSScriptRoot
$pythonPath = Join-Path $projectDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "가상환경 Python을 찾지 못했습니다: $pythonPath"
}
if ($Clean) {
    Remove-Item -Recurse -Force (Join-Path $projectDir ".build_launcher"), (Join-Path $projectDir "release") -ErrorAction SilentlyContinue
}

$payloadDir = Join-Path $env:TEMP "MokpoYouthPolicyLauncherPayload"
Remove-Item -Recurse -Force $payloadDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $payloadDir | Out-Null
$excludedDirectories = @(".git", ".venv", "__pycache__", "data", "logs", "build", "release", ".build_launcher", ".launcher_payload", ".pptx_build_screen_design", "node_modules", "dist", ".vite")
& robocopy $projectDir $payloadDir /E /XD $excludedDirectories /XF ".env" "MokpoYouthPolicyLauncher.spec" | Out-Null
if ($LASTEXITCODE -gt 7) {
    throw "실행파일 포함용 서비스 파일 복사에 실패했습니다. 종료 코드: $LASTEXITCODE"
}

$arguments = @(
    "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN", "--onefile", "--windowed",
    "--name", "MokpoYouthPolicyLauncher", "--collect-all", "cryptography",
    "--add-data", "$payloadDir;app",
    "--distpath", (Join-Path $projectDir "release"),
    "--workpath", (Join-Path $projectDir ".build_launcher"),
    (Join-Path $projectDir "mokpo_youth_launcher.py")
)
& $pythonPath @arguments
if ($LASTEXITCODE -ne 0) {
    throw "실행파일 생성에 실패했습니다. 종료 코드: $LASTEXITCODE"
}
Remove-Item -Recurse -Force $payloadDir -ErrorAction SilentlyContinue
Write-Host "완료: $(Join-Path $projectDir 'release\MokpoYouthPolicyLauncher.exe')"
