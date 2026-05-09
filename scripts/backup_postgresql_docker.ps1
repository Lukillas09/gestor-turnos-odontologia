param(
    [string]$EnvFile = ".env",
    [string]$OutputDir = "backups",
    [string]$DockerImage = "postgres:17-alpine"
)

$ErrorActionPreference = "Stop"

$databaseUrl = $env:DATABASE_URL

if ([string]::IsNullOrWhiteSpace($databaseUrl) -and (Test-Path $EnvFile)) {
    $databaseUrlLine = Get-Content $EnvFile |
        Where-Object { $_ -match '^DATABASE_URL=' } |
        Select-Object -First 1

    if ($databaseUrlLine) {
        $databaseUrl = $databaseUrlLine.Substring("DATABASE_URL=".Length).Trim().Trim('"').Trim("'")
    }
}

if ([string]::IsNullOrWhiteSpace($databaseUrl)) {
    throw "Falta DATABASE_URL. Definila como variable de entorno o en $EnvFile."
}

New-Item -ItemType Directory -Force $OutputDir | Out-Null

$backupFile = "postgresql-public-$((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')).dump"
$resolvedOutputDir = (Resolve-Path $OutputDir).Path -replace "\\", "/"

docker run --rm `
    -e "DATABASE_URL=$databaseUrl" `
    -e "BACKUP_FILE=$backupFile" `
    -v "${resolvedOutputDir}:/backups" `
    $DockerImage `
    sh -lc 'pg_dump "$DATABASE_URL" --format=custom --no-owner --no-acl --schema=public --file="/backups/$BACKUP_FILE"'

if ($LASTEXITCODE -ne 0) {
    throw "No se pudo crear el backup PostgreSQL."
}

Write-Output "Backup creado: $OutputDir/$backupFile"
