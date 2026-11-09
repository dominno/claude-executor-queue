# Same options and implementation as install.sh; no network or elevated rights.
$ErrorActionPreference = 'Stop'
$helper = Join-Path $PSScriptRoot 'scripts/install.py'
foreach ($candidate in @('py', 'python', 'python3')) {
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notmatch '[\\/]WindowsApps[\\/]') {
        $prefix = @()
        if ($candidate -eq 'py') { $prefix = @('-3') }
        $usable = $false
        try {
            & $command.Source @prefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>$null
            $usable = ($LASTEXITCODE -eq 0)
        } catch {
            # PowerShell 5.1 may turn native probe stderr into a terminating error.
            $usable = $false
        }
        if ($usable) {
            & $command.Source @prefix $helper @args
            exit $LASTEXITCODE
        }
    }
}
Write-Error 'install: Python 3.8+ is required'
exit 1
