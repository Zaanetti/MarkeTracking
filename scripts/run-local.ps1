$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $repoRoot ".env.local"

function New-RandomHex([int]$byteCount) {
    $bytes = New-Object byte[] $byteCount
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return [BitConverter]::ToString($bytes).Replace("-", "")
}

function New-FernetKey {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_")
}

Push-Location $repoRoot
try {
    if (-not (Test-Path $configPath)) {
        $databasePassword = New-RandomHex 32
        $minioPassword = New-RandomHex 32
        $encryptionKey = New-FernetKey
        $config = @"
POSTGRES_DB=marketracking
POSTGRES_USER=marketracking_local
POSTGRES_PASSWORD=$databasePassword
MINIO_ROOT_USER=marketracking_local
MINIO_ROOT_PASSWORD=$minioPassword
FAILED_HTML_ENCRYPTION_KEY=$encryptionKey
"@
        [System.IO.File]::WriteAllText(
            $configPath,
            $config,
            [System.Text.UTF8Encoding]::new($false)
        )
        Write-Host "Generated local-only credentials in ignored .env.local."
    }

    docker compose --env-file .env.local -f docker-compose.local.yml up --build -d
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed to start the local stack."
    }

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8001/api/ready" -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }

    if (-not $ready) {
        docker compose --env-file .env.local -f docker-compose.local.yml ps
        throw "The local API did not become ready within 120 seconds."
    }

    Write-Host "Local API is ready at http://127.0.0.1:8001"
    Write-Host "MinIO console: http://127.0.0.1:9003"
    docker compose --env-file .env.local -f docker-compose.local.yml ps
}
finally {
    Pop-Location
}
