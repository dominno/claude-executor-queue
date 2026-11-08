# Same options and implementation as install.sh; no network or elevated rights.
$ErrorActionPreference = 'Stop'
$helper = Join-Path $PSScriptRoot 'scripts/install.py'
foreach ($candidate in @('python3', 'python', 'py')) {
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command) {
        $prefix = @()
        if ($candidate -eq 'py') { $prefix = @('-3') }
        & $command.Source @prefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) {
            & $command.Source @prefix $helper @args
            exit $LASTEXITCODE
        }
    }
}
Write-Error 'install: Python 3.8+ is required'
exit 1
