$ErrorActionPreference = 'Stop'

Write-Host "[setup] Detecting Python..."
function Find-Python {
  if (Get-Command python -ErrorAction SilentlyContinue) { return 'python' }
  elseif (Get-Command py -ErrorAction SilentlyContinue) { return 'py' }
  else { throw "Python not found. Please install Python 3.10+ and retry." }
}
$py = Find-Python

Write-Host "[setup] Creating virtual environment at .venv (if missing)..."
if (-not (Test-Path .venv)) {
  & $py -m venv .venv
}

$venvPy = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { throw "Venv python not found at $venvPy" }

Write-Host "[setup] Bootstrapping pip inside venv..."
try {
  & $venvPy -m pip --version | Out-Null
} catch {
  try {
    & $venvPy -m ensurepip --upgrade | Out-Null
  } catch {
    Write-Host "[setup] ensurepip not available; downloading get-pip.py..."
    $tmp = Join-Path $env:TEMP 'get-pip.py'
    Invoke-WebRequest -UseBasicParsing https://bootstrap.pypa.io/get-pip.py -OutFile $tmp
    & $venvPy $tmp
  }
}

Write-Host "[setup] Upgrading build tooling..."
& $venvPy -m pip install -U pip setuptools wheel

Write-Host "[setup] Installing requirements..."
& $venvPy -m pip install -r requirements.txt

Write-Host "[setup] Generating Prisma client and pushing schema (SQLite)..."
try { & $venvPy -m prisma generate } catch {}
try { & $venvPy -m prisma db push } catch {}

Write-Host "[setup] Done. Next steps:"
@"

1) Activate the venv for this shell:
   .\.venv\Scripts\activate

2) Sync channels to SQLite (requires Bot token in env):
   setx DISCORD_TOKEN_TYPE Bot
   setx GUILD_ID 1384033183112237208
   $env:DISCORD_TOKEN_TYPE='Bot'; $env:TOKEN='YOUR_BOT_TOKEN'; python -m digest --sync-channels

3) Launch the TUI:
   python -m tui

"@ | Write-Host

