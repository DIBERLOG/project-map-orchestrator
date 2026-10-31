$ErrorActionPreference = 'Stop'
$mapRoot = $PSScriptRoot
$mapPython = (Get-Command python -ErrorAction Stop).Source
$mapPythonWindowless = Join-Path (Split-Path $mapPython -Parent) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $mapPythonWindowless)) { throw 'pythonw.exe is required for hidden launch' }
$mapShell = New-Object -ComObject WScript.Shell
$mapDesktop = [Environment]::GetFolderPath('Desktop')
$mapStartup = [Environment]::GetFolderPath('Startup')
foreach ($mapDestination in @(@{Folder=$mapDesktop;Name='pmap.lnk';Args=''},@{Folder=$mapStartup;Name='pmap-autostart.lnk';Args=' --no-browser'})) {
    $mapLinkPath = Join-Path $mapDestination.Folder $mapDestination.Name
    if (Test-Path -LiteralPath $mapLinkPath) {
        $mapExisting = $mapShell.CreateShortcut($mapLinkPath)
        if ($mapExisting.Arguments -notlike '*Codex-Project-Map*launch.py*') { throw "An unrelated shortcut already exists: $mapLinkPath" }
    }
    $mapShortcut = $mapShell.CreateShortcut($mapLinkPath)
    $mapShortcut.TargetPath = $mapPythonWindowless
    $mapShortcut.Arguments = '"' + (Join-Path $mapRoot 'launch.py') + '"' + $mapDestination.Args
    $mapShortcut.WorkingDirectory = $mapRoot
    $mapShortcut.Description = 'pmap — карта проекта и локальный оркестратор'
    $mapShortcut.WindowStyle = 7
    $mapShortcut.Save()
    Write-Output $mapLinkPath
}
