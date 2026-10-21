# Runs the already cached Modal client in WSL. No local package installation.
$ErrorActionPreference = 'Stop'
$workerPath = '/mnt/' + $PSScriptRoot.Substring(0,1).ToLower() + $PSScriptRoot.Substring(2).Replace('\','/') + '/launch.py'
$env:WSLENV = (($env:WSLENV, 'UV_OFFLINE') | Where-Object { $_ }) -join ':'
$env:UV_OFFLINE = '1'
$consoleDir = Join-Path $PSScriptRoot 'runs'
New-Item -ItemType Directory -Force -Path $consoleDir | Out-Null
$consolePath = Join-Path $consoleDir ('console-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.log')
& wsl.exe --exec /home/tung/.local/bin/uv tool run --offline --from modal python $workerPath @args 2>&1 | Tee-Object -FilePath $consolePath
exit $LASTEXITCODE
