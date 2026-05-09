param(
    [string]$BackupPath = "",
    [string]$DockerImage = "postgres:17-alpine",
    [string]$ContainerName = "gestor-turnos-restore-test"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($BackupPath)) {
    $latestBackup = Get-ChildItem "backups\postgresql-public-*.dump" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if (-not $latestBackup) {
        throw "No encontre backups en backups\postgresql-public-*.dump."
    }

    $BackupPath = $latestBackup.FullName
}

if (-not (Test-Path $BackupPath)) {
    throw "No existe el backup indicado: $BackupPath"
}

$existingContainer = docker ps -a --format "{{.Names}}" | Where-Object { $_ -eq $ContainerName }
if ($existingContainer) {
    docker rm -f $ContainerName | Out-Null
}

$backup = Get-Item $BackupPath
$backupDir = (Resolve-Path $backup.DirectoryName).Path -replace "\\", "/"
$backupFile = $backup.Name
$restoreDatabaseUrl = "postgresql://postgres:restore_test_password@localhost:5432/restore_test"

docker run -d `
    --name $ContainerName `
    -e POSTGRES_PASSWORD=restore_test_password `
    -e POSTGRES_DB=restore_test `
    $DockerImage | Out-Null

try {
    $deadline = (Get-Date).AddMinutes(2)
    $ready = $false

    do {
        Start-Sleep -Seconds 2
        docker exec $ContainerName pg_isready -U postgres -d restore_test | Out-Null
        $ready = $LASTEXITCODE -eq 0
    } while (-not $ready -and (Get-Date) -lt $deadline)

    if (-not $ready) {
        throw "PostgreSQL temporal no inicio a tiempo."
    }

    docker run --rm `
        --network "container:$ContainerName" `
        -e "RESTORE_DATABASE_URL=$restoreDatabaseUrl" `
        -e "BACKUP_FILE=$backupFile" `
        -v "${backupDir}:/backups" `
        $DockerImage `
        sh -lc 'pg_restore --dbname="$RESTORE_DATABASE_URL" --no-owner --no-acl --clean --if-exists --exit-on-error "/backups/$BACKUP_FILE"'

    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo restaurar el backup."
    }

    $tableCount = docker exec $ContainerName psql -U postgres -d restore_test -tAc "select count(*) from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE';"
    $migrationCount = docker exec $ContainerName psql -U postgres -d restore_test -tAc "select count(*) from django_migrations;"
    $turnoCount = docker exec $ContainerName psql -U postgres -d restore_test -tAc "select count(*) from turnos_turno;"
    $pacienteCount = docker exec $ContainerName psql -U postgres -d restore_test -tAc "select count(*) from pacientes_paciente;"

    [PSCustomObject]@{
        Backup = $backupFile
        TablasPublicas = $tableCount.Trim()
        Migraciones = $migrationCount.Trim()
        Turnos = $turnoCount.Trim()
        Pacientes = $pacienteCount.Trim()
    }
}
finally {
    $existingContainer = docker ps -a --format "{{.Names}}" | Where-Object { $_ -eq $ContainerName }
    if ($existingContainer) {
        docker rm -f $ContainerName | Out-Null
    }
}
