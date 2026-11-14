[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Import-DotEnv {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $value = $line.Trim()
        if (-not $value -or $value.StartsWith("#")) {
            continue
        }
        if ($value.StartsWith("export ")) {
            $value = $value.Substring(7).Trim()
        }
        $separator = $value.IndexOf("=")
        if ($separator -lt 1) {
            throw "Invalid .env line: $line"
        }
        $name = $value.Substring(0, $separator).Trim()
        $content = $value.Substring($separator + 1).Trim()
        if (($content.StartsWith('"') -and $content.EndsWith('"')) -or
            ($content.StartsWith("'") -and $content.EndsWith("'"))) {
            $content = $content.Substring(1, $content.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $content, "Process")
    }
}

function Find-PostgresCommand {
    param([string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }
    foreach ($installRoot in $roots) {
        $postgresRoot = Join-Path $installRoot "PostgreSQL"
        if (-not (Test-Path -LiteralPath $postgresRoot)) {
            continue
        }
        $matches = Get-ChildItem -LiteralPath $postgresRoot -Directory |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "bin\$Name.exe" } |
            Where-Object { Test-Path -LiteralPath $_ }
        if ($matches) {
            return $matches[0]
        }
    }
    throw "$Name was not found. Install PostgreSQL or add its bin directory to PATH, or set DATABASE_URL."
}

Import-DotEnv (Join-Path $root ".env")

$python = $env:CINECONTEXT_PYTHON
if (-not $python) {
    $venvPython = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        $python = $venvPython
    } else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) {
            throw "Python was not found. Create .venv or set CINECONTEXT_PYTHON."
        }
        $python = $pythonCommand.Source
    }
}

& $python -c "import alcedo, asyncpg, openai, httpx" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -e (Join-Path $root "backend")
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed." }
}

if (-not $env:DATABASE_URL) {
    $runtime = Join-Path $root ".runtime"
    $dbDir = Join-Path $runtime "postgres"
    $passwordFile = Join-Path $runtime "db-password"
    $logFile = Join-Path $runtime "postgres.log"
    $dbPort = if ($env:CINECONTEXT_DB_PORT) { $env:CINECONTEXT_DB_PORT } else { "55438" }
    $initdb = Find-PostgresCommand "initdb"
    $pgCtl = Find-PostgresCommand "pg_ctl"

    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    if (-not (Test-Path -LiteralPath (Join-Path $dbDir "PG_VERSION"))) {
        $password = & $python -c "import secrets; print(secrets.token_urlsafe(24))"
        [System.IO.File]::WriteAllText($passwordFile, $password)
        & $initdb -D $dbDir -U cinecontext "--auth=scram-sha-256" "--pwfile=$passwordFile" --no-locale -E UTF8
        if ($LASTEXITCODE -ne 0) { throw "PostgreSQL initialization failed." }
    }

    & $pgCtl -D $dbDir status *> $null
    if ($LASTEXITCODE -ne 0) {
        & $pgCtl -D $dbDir -l $logFile -o "-p $dbPort -c listen_addresses=127.0.0.1" -w start
        if ($LASTEXITCODE -ne 0) { throw "PostgreSQL startup failed. See $logFile." }
    }
    $password = [System.IO.File]::ReadAllText($passwordFile).Trim()
    $env:DATABASE_URL = "postgresql+asyncpg://cinecontext:$password@127.0.0.1:$dbPort/postgres"
}

Push-Location (Join-Path $root "backend")
try {
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }
} finally {
    Pop-Location
}

& npm --prefix (Join-Path $root "frontend") ci --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }
& npm --prefix (Join-Path $root "frontend") run build
if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }

$appMode = if ($env:CINECONTEXT_APP) { $env:CINECONTEXT_APP } else { "mock" }
$appFactory = switch ($appMode) {
    "mock" { "cinecontext.mock.main:create_app" }
    "production" { "cinecontext.main:create_app" }
    default { throw "CINECONTEXT_APP must be mock or production." }
}
$port = if ($env:CINECONTEXT_PORT) { $env:CINECONTEXT_PORT } else { "8765" }

Push-Location (Join-Path $root "backend")
try {
    & $python -m uvicorn $appFactory --factory --host 127.0.0.1 --port $port
} finally {
    Pop-Location
}
