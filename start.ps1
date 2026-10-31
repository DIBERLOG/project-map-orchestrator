$ErrorActionPreference = 'Stop'
$mapRoot = $PSScriptRoot
$mapPython = (Get-Command python -ErrorAction Stop).Source
& $mapPython (Join-Path $mapRoot 'launch.py') --no-browser
if ($LASTEXITCODE -ne 0) { throw 'pmap could not start; see outputs/launcher-error.log' }
