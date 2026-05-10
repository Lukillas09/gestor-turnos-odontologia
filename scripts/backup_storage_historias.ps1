param(
    [string]$Python = "python",
    [string]$OutputDir = "..\backups\storage",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

Push-Location "$PSScriptRoot\..\app"

try {
    $argumentos = @(
        "manage.py",
        "backup_storage_historias",
        "--output-dir",
        $OutputDir
    )

    if ($DryRun) {
        $argumentos += "--dry-run"
    }

    & $Python @argumentos

    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo crear el backup de Storage."
    }
}
finally {
    Pop-Location
}
