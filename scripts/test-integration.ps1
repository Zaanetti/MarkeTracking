$ErrorActionPreference = "Stop"

$token = [Guid]::NewGuid().ToString("N").Substring(0, 10)
$postgresName = "mt-integration-pg-$token"
$minioName = "mt-integration-minio-$token"
$postgresStarted = $false
$minioStarted = $false

try {
    Write-Host "Starting disposable PostgreSQL and MinIO containers..."

    docker run --rm --detach `
        --name $postgresName `
        --publish "127.0.0.1::5432" `
        --tmpfs "/var/lib/postgresql/data:rw,size=512m" `
        --env "POSTGRES_DB=marketracking_test" `
        --env "POSTGRES_USER=mt_test" `
        --env "POSTGRES_PASSWORD=mt_test_$token" `
        postgres:16 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not start disposable PostgreSQL." }
    $postgresStarted = $true

    docker run --rm --detach `
        --name $minioName `
        --publish "127.0.0.1::9000" `
        --tmpfs "/data:rw,size=512m" `
        --env "MINIO_ROOT_USER=mtintegration" `
        --env "MINIO_ROOT_PASSWORD=mtintegration_$token`!" `
        minio/minio:latest server /data --console-address :9001 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not start disposable MinIO." }
    $minioStarted = $true

    $postgresReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        docker exec $postgresName pg_isready -U mt_test -d marketracking_test | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $postgresReady = $true
            break
        }
        Start-Sleep -Seconds 2
    }
    if (-not $postgresReady) { throw "Disposable PostgreSQL did not become ready." }

    $minioPortMapping = docker port $minioName 9000/tcp
    if ($LASTEXITCODE -ne 0) { throw "Could not determine the MinIO port." }
    $minioPort = ($minioPortMapping -split ":")[-1].Trim()
    $minioReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            Invoke-WebRequest -UseBasicParsing `
                -Uri "http://127.0.0.1:$minioPort/minio/health/live" `
                -TimeoutSec 2 | Out-Null
            $minioReady = $true
            break
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $minioReady) { throw "Disposable MinIO did not become ready." }

    $postgresPortMapping = docker port $postgresName 5432/tcp
    if ($LASTEXITCODE -ne 0) { throw "Could not determine the PostgreSQL port." }
    $postgresPort = ($postgresPortMapping -split ":")[-1].Trim()
    $keyBytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($keyBytes)
    $fernetKey = [Convert]::ToBase64String($keyBytes).TrimEnd("=").Replace("+", "-").Replace("/", "_") + "="

    $env:APP_ENV = "integration"
    $env:MARKETRACKING_INTEGRATION_TESTS = "1"
    $env:DATABASE_URL = "postgresql+psycopg://mt_test:mt_test_$token@127.0.0.1:$postgresPort/marketracking_test"
    $env:STORAGE_ENDPOINT = "127.0.0.1:$minioPort"
    $env:STORAGE_ACCESS_KEY = "mtintegration"
    $env:STORAGE_SECRET_KEY = "mtintegration_$token!"
    $env:STORAGE_BUCKET = "mt-integration-$token"
    $env:STORAGE_SECURE = "false"
    $env:FAILED_HTML_ENCRYPTION_KEY = $fernetKey

    Write-Host "Applying migrations to the disposable database..."
    python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Alembic migration failed." }
    python -m alembic downgrade -1
    if ($LASTEXITCODE -ne 0) { throw "Alembic downgrade validation failed." }
    python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Alembic re-upgrade validation failed." }

    Write-Host "Running the complete test suite, including PostgreSQL and MinIO integration tests..."
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "The test suite failed." }
}
finally {
    foreach ($containerName in @($postgresName, $minioName)) {
        $container = docker ps -aq --filter "name=^/$containerName$"
        if ($container) {
            docker stop $containerName | Out-Null
        }
    }
}
