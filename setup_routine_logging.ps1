<#
.SYNOPSIS
    Sets up (or removes) routine logging from a Vernier GDX-CO2 with Windows Task Scheduler.

.DESCRIPTION
    Registers a Task Scheduler task that runs humidity_logger.py in the background
    (no console window) every time you log in. If the logger stops for any reason,
    for example because the sensor was unplugged, the task starts it again within
    5 minutes. Only one copy runs at a time.

    Run it from the folder containing humidity_logger.py:

        powershell -ExecutionPolicy Bypass -File .\setup_routine_logging.ps1

.EXAMPLE
    .\setup_routine_logging.ps1 -StartNow
    Log every 60 s to humidity_log.csv next to the script, starting right away.

.EXAMPLE
    .\setup_routine_logging.ps1 -MySQL -StartNow
    Insert every 60 s row into MySQL (settings in db_config.ini), keeping a CSV backup.

.EXAMPLE
    .\setup_routine_logging.ps1 -Interval 300 -Output D:\data\lab_rh.csv
    Log 5-minute averages to D:\data\lab_rh.csv, starting at next logon.

.EXAMPLE
    .\setup_routine_logging.ps1 -Uninstall
    Remove the task (and stop the logger if it is running).
#>
[CmdletBinding()]
param(
    # Seconds averaged into each CSV row.
    [double]$Interval = 60,
    # Sensor sample period in milliseconds.
    [int]$SamplePeriod = 1000,
    # Seconds of readings discarded at start.
    [double]$Warmup = 90,
    # Stop after logging this many seconds (0 = run until stopped). Mainly for testing.
    [double]$Duration = 0,
    # CSV file to append to. Defaults to humidity_log.csv next to this script.
    [string]$Output,
    # Also insert each row into MySQL, using the settings in -DbConfig.
    [switch]$MySQL,
    # MySQL settings file. Defaults to db_config.ini next to this script.
    [string]$DbConfig,
    # Don't write the CSV backup (only with -MySQL).
    [switch]$NoCsv,
    # Status/error log. Defaults to humidity_logger.log next to this script.
    [string]$LogFile,
    # Connect over Bluetooth instead of USB.
    [switch]$Ble,
    # Log only relative humidity.
    [switch]$HumidityOnly,
    # Minutes between Task Scheduler checks that restart the logger if it stopped.
    [int]$RestartMinutes = 5,
    # Path to python.exe or pythonw.exe. Found automatically if omitted.
    [string]$PythonPath,
    [string]$TaskName = "GDX-CO2 Humidity Logger",
    # Start the logger immediately instead of waiting for the next logon.
    [switch]$StartNow,
    # Remove the task instead of creating it.
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$dir = $PSScriptRoot
$script = Join-Path $dir "humidity_logger.py"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed task '$TaskName'."
    } else {
        Write-Host "No task named '$TaskName' found."
    }
    return
}

if (-not (Test-Path $script)) {
    throw "humidity_logger.py not found next to this script ($dir)."
}

# Find a real Python install (not the Microsoft Store shortcut) and use pythonw.exe
# so the logger runs without a console window.
function Find-Python {
    # Windows PowerShell treats native stderr as an error under "Stop"; relax it here.
    $ErrorActionPreference = "Continue"
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += (& py -3 -c "import sys; print(sys.executable)" 2>$null)
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike "*WindowsApps*") {
        $candidates += (& $cmd.Source -c "import sys; print(sys.executable)" 2>$null)
    }
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c.Trim() }
    }
    return $null
}

if (-not $PythonPath) { $PythonPath = Find-Python }
if (-not $PythonPath -or -not (Test-Path $PythonPath)) {
    throw "Python not found. Install Python 3 from python.org (tick 'Add python.exe to PATH') or pass -PythonPath."
}
$python = $PythonPath -replace "pythonw\.exe$", "python.exe"
$pythonw = $python -replace "python\.exe$", "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python }

$hasGodirect = & {
    $ErrorActionPreference = "Continue"
    & $python -c "import godirect" 2>$null
    $LASTEXITCODE -eq 0
}
if (-not $hasGodirect) {
    throw "The godirect package is not installed for $python. Run: `"$python`" -m pip install godirect"
}

if ($NoCsv -and -not $MySQL) { throw "-NoCsv needs -MySQL, otherwise nothing would be saved." }
if (-not $Output)  { $Output = Join-Path $dir "humidity_log.csv" }
if (-not $LogFile) { $LogFile = Join-Path $dir "humidity_logger.log" }
$Output = [System.IO.Path]::GetFullPath($Output)
$LogFile = [System.IO.Path]::GetFullPath($LogFile)

if ($MySQL) {
    if (-not $DbConfig) { $DbConfig = Join-Path $dir "db_config.ini" }
    $DbConfig = [System.IO.Path]::GetFullPath($DbConfig)
    # Check the database before registering, so a bad password shows up now
    # rather than silently in the background.
    Write-Host "Checking MySQL settings in $DbConfig ..."
    $dbOk = & {
        $ErrorActionPreference = "Continue"
        & $python $script --test-db --db-config $DbConfig | Out-Host
        $LASTEXITCODE -eq 0
    }
    if (-not $dbOk) { throw "MySQL check failed (see message above). Fix it and run this again." }
}

$argList = @("`"$script`"", "--output", "`"$Output`"", "--log-file", "`"$LogFile`"",
             "--interval", $Interval, "--sample-period", $SamplePeriod, "--warmup", $Warmup)
if ($MySQL)          { $argList += @("--mysql", "--db-config", "`"$DbConfig`"") }
if ($NoCsv)          { $argList += "--no-csv" }
if ($Duration -gt 0) { $argList += @("--duration", $Duration) }
if ($Ble)            { $argList += "--ble" }
if ($HumidityOnly)   { $argList += "--humidity-only" }
$arguments = $argList -join " "

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
# Re-check every few minutes; IgnoreNew below means a running logger is left alone.
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $RestartMinutes)).Repetition
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force `
    -Description "Logs RH/CO2/temperature from a Vernier GDX-CO2 (see $LogFile)" | Out-Null

Write-Host "Registered task '$TaskName'."
Write-Host "  Python : $pythonw"
Write-Host "  Args   : $arguments"
if (-not $NoCsv) { Write-Host "  CSV    : $Output" }
if ($MySQL)      { Write-Host "  MySQL  : settings in $DbConfig" }
Write-Host "  Log    : $LogFile"

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started. The first row appears after the $Warmup s warm-up plus $Interval s."
} else {
    Write-Host "It starts at your next logon (or run again with -StartNow)."
}
Write-Host "Remove it with: .\setup_routine_logging.ps1 -Uninstall"
