# Runs the already cached Modal client in WSL. No local package installation.
$ErrorActionPreference = 'Stop'
$workerPath = '/mnt/' + $PSScriptRoot.Substring(0,1).ToLower() + $PSScriptRoot.Substring(2).Replace('\','/') + '/launch.py'
$env:WSLENV = (($env:WSLENV, 'UV_OFFLINE') | Where-Object { $_ }) -join ':'
$env:UV_OFFLINE = '1'
& wsl.exe --exec /home/tung/.local/bin/uv tool run --offline --from modal python $workerPath @args
exit $LASTEXITCODE
