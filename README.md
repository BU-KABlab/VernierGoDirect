# VernierGoDirect

Routine logging of relative humidity, CO2 and temperature from a
[Vernier Go Direct CO2 Gas Sensor (GDX-CO2)](https://www.vernier.com/product/go-direct-co2-gas-sensor/)
on Windows, using Vernier's [`godirect`](https://pypi.org/project/godirect/) Python library.

| File | What it does |
| --- | --- |
| `humidity_logger.py` | Connects to the sensor (USB or Bluetooth), discards a warm-up period, and appends one averaged row per interval to a CSV file. |
| `setup_routine_logging.ps1` | Registers a Windows Task Scheduler task that runs the logger in the background every time you log in, and restarts it if it stops. |

## Setup

1. Install Python 3 from [python.org](https://www.python.org/downloads/) with
   **"Add python.exe to PATH"** ticked. Then turn off the `python.exe` and `python3.exe`
   entries under *Settings → Apps → Advanced app settings → App execution aliases*.

   Or, with winget:

   ```powershell
   winget install --id Python.Python.3.13 -e --scope user
   ```

2. Install the dependencies (open a new terminal first so `py` is on PATH):

   ```powershell
   py -m pip install -r requirements.txt
   ```

3. Plug in the GDX-CO2 over USB and switch it on.

## One-off logging

```powershell
py humidity_logger.py
```

This samples every 1000 ms, discards the first 90 s as warm-up, then appends one averaged
row per 60 s to `humidity_log.csv`. Press Ctrl+C to stop.

| Option | Default | Meaning |
| --- | --- | --- |
| `--interval` | 60 | Seconds averaged into each CSV row |
| `--sample-period` | 1000 | Sensor sample period in ms |
| `--warmup` | 90 | Seconds of readings discarded at start |
| `--duration` | run until stopped | Seconds of logging after warm-up, then exit |
| `--output` | `humidity_log.csv` | CSV file to append to |
| `--ble` | off | Connect over Bluetooth instead of USB |
| `--humidity-only` | off | Log only relative humidity |

Quick check: 10 s rows for 1 minute (about 2.5 minutes including warm-up):

```powershell
py humidity_logger.py --interval 10 --duration 60 --output test.csv
```

Example output:

```
timestamp,samples,humidity_pct,co2_ppm,temperature_C
2026-10-14T15:49:52,11,51.09,560.91,24.47
2026-10-14T15:50:02,10,52.27,584.03,24.46
2026-10-14T15:50:12,10,51.7,593.5,24.54
```

`samples` is how many readings went into the averages on that row.

## Routine logging (Task Scheduler)

From this folder, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_routine_logging.ps1 -StartNow
```

This creates a task named **GDX-CO2 Humidity Logger** that:

- starts the logger at every logon, in the background with no console window;
- checks every 5 minutes and restarts the logger if it has stopped (for example, the sensor
  was unplugged), without ever running two copies;
- has no time limit and keeps running on battery.

Data goes to `humidity_log.csv` in this folder unless you pass `-Output`. The setup script
accepts the same settings as the logger: `-Interval`, `-SamplePeriod`, `-Warmup`,
`-Duration`, `-Output`, `-Ble`, `-HumidityOnly`, plus `-RestartMinutes`, `-PythonPath`
and `-TaskName`. Leave out `-StartNow` to wait until the next logon.

To test the task first, log 10 s rows for 1 minute to a separate file:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_routine_logging.ps1 -Interval 10 -Duration 60 -Output test.csv -StartNow
```

Then re-run the setup without `-Duration` to switch to continuous logging. With
`-Duration`, the logger exits after each run, and the task starts a new run within
`-RestartMinutes`.

To remove the task:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_routine_logging.ps1 -Uninstall
```

Because the logger runs without a window, check it is working by looking for new rows in the
CSV, or in Task Scheduler (*Last Run Result* `0x0` means a clean exit; `0x41301` means
it is running).

## Notes

- Channels are found by name ("Relative Humidity", "CO₂ Gas", "Temperature"), not fixed
  channel numbers.
- The first row of a run can average a couple of extra samples left over from the warm-up.
- CSV files are ignored by git, so measurement data stays on the logging PC.
