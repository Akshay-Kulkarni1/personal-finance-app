# Ahead Phase 0 local launcher (Windows PowerShell).
param([switch]$Setup)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
if ($Setup) {
  if (!(Get-Command npm -ErrorAction SilentlyContinue)) { throw 'Install Node.js LTS (includes npm) first.' }
  py -m venv (Join-Path $backend '.venv')
  if ($LASTEXITCODE -ne 0) { throw 'Unable to create Python environment' }
  & $python -m pip install -r (Join-Path $backend 'requirements.txt')
  if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
  Push-Location $frontend
  try { npm ci; if ($LASTEXITCODE -ne 0) { throw 'npm install failed' } }
  finally { Pop-Location }
}
if (!(Test-Path $python) -or !(Test-Path (Join-Path $frontend 'node_modules'))) {
  throw 'Dependencies missing. Run: .\scripts\start-local.ps1 -Setup'
}
& $python (Join-Path $root 'scripts\configure-local.py')
if ($LASTEXITCODE -ne 0) { throw 'Unable to configure local environment' }
& $python (Join-Path $root 'scripts\doctor-local.py') --preflight
if ($LASTEXITCODE -ne 0) { throw 'Local startup preflight failed' }
$api = Start-Process powershell -PassThru -ArgumentList @('-NoExit','-Command',"Set-Location '$backend'; & '$python' -m uvicorn main:app --reload --host 127.0.0.1 --port 8000")
& $python (Join-Path $root 'scripts\doctor-local.py') --wait-api --timeout 25
if ($LASTEXITCODE -ne 0) {
  Write-Warning 'The backend did not start. Read the FastAPI terminal window for the error.'
  throw 'Ahead API startup failed — the frontend was not started.'
}
$worker = Start-Process powershell -PassThru -ArgumentList @('-NoExit','-Command',"Set-Location '$backend'; & '$python' worker.py --poll 30")
$web = Start-Process powershell -PassThru -ArgumentList @('-NoExit','-Command',"Set-Location '$frontend'; npm run dev -- --hostname 127.0.0.1 --port 3000")
Write-Host 'Ahead launched in three terminal windows:'
Write-Host ' Website:   http://127.0.0.1:3000'
Write-Host ' API proxy: http://127.0.0.1:3000/api/ahead/health'
Write-Host ' API docs:  http://127.0.0.1:8000/docs'
Write-Host ' Diagnose:  & .\backend\.venv\Scripts\python.exe .\scripts\doctor-local.py'
Write-Host ' Verify:    & .\backend\.venv\Scripts\python.exe .\scripts\smoke-local.py'
Write-Host ' Close the three windows to stop Ahead.'
