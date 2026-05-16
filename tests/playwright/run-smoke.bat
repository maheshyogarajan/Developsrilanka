@echo off
:: ============================================================
:: FIESTA Smoke Test Runner (Windows)
:: Usage:
::   run-smoke.bat
::   set BASE_URL=https://fiesta.developsrilanka.com && run-smoke.bat
::   set TEST_EMAIL=me@example.com && set TEST_PASSWORD=pass && run-smoke.bat
:: ============================================================
setlocal enabledelayedexpansion

cd /d "%~dp0"

:: ── 1. Resolve BASE_URL ─────────────────────────────────────
if "%BASE_URL%"=="" (
    set INFRA_REPORT=G:\My Drive\CEO OS\working files\_cockpit_fiesta\SUB_C_INFRA_REPORT.md
    if exist "!INFRA_REPORT!" (
        :: Extract first https:// URL from the report
        for /f "tokens=*" %%L in ('powershell -NoProfile -Command "Select-String -Path '!INFRA_REPORT!' -Pattern 'https://[^\s]+' | ForEach-Object { $_.Matches.Value } | Where-Object { $_ -notmatch 'metrics|supabase|example' } | Select-Object -First 1"') do (
            set BASE_URL=%%L
        )
    )
    if "%BASE_URL%"=="" (
        set BASE_URL=https://fiesta-mvp.fly.dev
        echo [run-smoke] BASE_URL defaulted to: %BASE_URL%
    ) else (
        echo [run-smoke] BASE_URL from SUB_C_INFRA_REPORT: %BASE_URL%
    )
) else (
    echo [run-smoke] BASE_URL from env: %BASE_URL%
)

:: ── 2. Run Playwright ────────────────────────────────────────
if not exist test-results mkdir test-results

npx playwright test smoke/ --reporter=list --reporter=html --output=test-results

echo.
echo [run-smoke] Done.
echo [run-smoke] HTML report: %~dp0playwright-report\index.html
echo [run-smoke] JUnit XML:   %~dp0test-results\results.xml

endlocal
