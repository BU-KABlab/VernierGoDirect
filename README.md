# VernierGoDirect

Routine logging of relative humidity, CO2 and temperature from a
[Vernier Go Direct CO2 Gas Sensor (GDX-CO2)](https://www.vernier.com/product/go-direct-co2-gas-sensor/)
on Windows, into a MySQL table and/or a CSV file, using Vernier's
[`godirect`](https://pypi.org/project/godirect/) Python library.

| File | What it does |
| --- | --- |
| `humidity_logger.py` | Connects to the sensor (USB or Bluetooth), discards a warm-up period, and saves one averaged row per interval to MySQL and/or CSV. |
| `setup_routine_logging.ps1` | Registers a Windows Task Scheduler task that runs the logger in the background every time you log in, and restarts it if it stops. |
| `sql/setup.sql` | Creates the `vernier_godirect` database and `gdx_co2_readings` table. |
| `db_config.example.ini` | Template for `db_config.ini`, which holds the MySQL connection settings. |

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

## MySQL setup

The logger writes to this table (`sql/setup.sql`):

| Column | Type | Contents |
| --- | --- | --- |
| `id` | BIGINT, auto-increment | Row number |
| `recorded_at` | DATETIME | Local time at the end of the averaging window |
| `device` | VARCHAR(64) | Sensor name and serial, e.g. `GDX-CO2 0Q5005V3` |
| `host` | VARCHAR(64) | Computer the logger ran on |
| `interval_s` | INT | Averaging window, seconds |
| `samples` | SMALLINT | Readings averaged into the row |
| `humidity_pct` | DECIMAL(5,2) | Relative humidity, % |
| `co2_ppm` | DECIMAL(8,2) | CO2, ppm (NULL with `--humidity-only`) |
| `temperature_c` | DECIMAL(5,2) | Temperature, °C (NULL with `--humidity-only`) |

`(device, recorded_at)` is unique, so a retried insert never creates a duplicate row.

1. **Create the database and table.** From this folder, run the setup script as an admin
   account. It asks for that account's password:

   ```powershell
   mysql -u root -p -e "source sql/setup.sql"
   ```

   If `mysql` isn't recognized, use its full path, usually
   `"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe"`. You can also open
   `sql/setup.sql` in MySQL Workbench and run it there.

2. **Create a MySQL account for the logger.** It only needs to read and insert rows.
   Log in as root (`mysql -u root -p`), then run the following with a new password of
   your own in place of `choose-a-password`:

   ```sql
   CREATE USER 'gdx_logger'@'localhost' IDENTIFIED BY 'choose-a-password';
   GRANT SELECT, INSERT ON vernier_godirect.gdx_co2_readings TO 'gdx_logger'@'localhost';
   ```

   If MySQL runs on a different computer from the sensor, use `'gdx_logger'@'%'` (or the
   logging PC's address) in both lines instead of `'localhost'`.

3. **Give the logger the connection settings.** Copy the template, then open
   `db_config.ini` and set `password` (and `host` if MySQL is on another computer):

   ```powershell
   Copy-Item db_config.example.ini db_config.ini
   notepad db_config.ini
   ```

   `db_config.ini` is ignored by git, so the password is never committed.

4. **Check the connection** (no sensor needed):

   ```powershell
   py humidity_logger.py --test-db
   ```

   This should print `MySQL OK: gdx_logger@localhost:3306/vernier_godirect.gdx_co2_readings has 0 row(s).`

## One-off logging

```powershell
py humidity_logger.py --mysql
```

This samples every 1000 ms, discards the first 90 s as warm-up, then inserts one averaged
row per 60 s into MySQL. It also appends the row to `humidity_log.csv` as a local backup.
Press Ctrl+C to stop. Leave out `--mysql` to log to CSV only.

If MySQL becomes unreachable while logging, rows keep going to the CSV and are queued in
memory. They are inserted once the server is back (up to a week of 60 s rows).

| Option | Default | Meaning |
| --- | --- | --- |
| `--mysql` | off | Insert each row into the MySQL table |
| `--db-config` | `db_config.ini` next to the script | MySQL settings file |
| `--test-db` | | Check the MySQL connection and table, then exit |
| `--no-csv` | off | Don't write the CSV backup (needs `--mysql`) |
| `--output` | `humidity_log.csv` | CSV file to append to |
| `--log-file` | none | Also write status messages and errors to this file |
| `--interval` | 60 | Seconds averaged into each row |
| `--sample-period` | 1000 | Sensor sample period in ms |
| `--warmup` | 90 | Seconds of readings discarded at start |
| `--duration` | run until stopped | Seconds of logging after warm-up, then exit |
| `--ble` | off | Connect over Bluetooth instead of USB |
| `--humidity-only` | off | Log only relative humidity |

Quick check: 10 s rows for 1 minute (about 2.5 minutes including warm-up):

```powershell
py humidity_logger.py --mysql --interval 10 --duration 60 --output test.csv
```

Example CSV output:

```
timestamp,samples,humidity_pct,co2_ppm,temperature_C
2026-10-14T15:49:52,11,51.09,560.91,24.47
2026-10-14T15:50:02,10,52.27,584.03,24.46
2026-10-14T15:50:12,10,51.7,593.5,24.54
```

`samples` is how many readings went into the averages on that row.

To look at the latest rows in MySQL:

```sql
SELECT recorded_at, humidity_pct, co2_ppm, temperature_c
FROM vernier_godirect.gdx_co2_readings
ORDER BY recorded_at DESC LIMIT 10;
```

## Routine logging (Task Scheduler)

From this folder, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_routine_logging.ps1 -MySQL -StartNow
```

With `-MySQL`, the setup script first runs `--test-db`. If the database settings are wrong,
it stops there instead of registering a task that would fail in the background.

This creates a task named **GDX-CO2 Humidity Logger** that:

- starts the logger at every logon, in the background with no console window;
- checks every 5 minutes and restarts the logger if it has stopped (for example, the sensor
  was unplugged), without ever running two copies;
- has no time limit and keeps running on battery.

Rows go to MySQL and to `humidity_log.csv` in this folder. Status messages and errors go to
`humidity_logger.log`. The setup script accepts the same settings as the logger: `-MySQL`,
`-DbConfig`, `-NoCsv`, `-Output`, `-LogFile`, `-Interval`, `-SamplePeriod`, `-Warmup`,
`-Duration`, `-Ble`, `-HumidityOnly`, plus `-RestartMinutes`, `-PythonPath` and
`-TaskName`. Leave out `-StartNow` to wait until the next logon. Leave out `-MySQL` to
log to CSV only.

To test the task first, log 10 s rows for 1 minute:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_routine_logging.ps1 -MySQL -Interval 10 -Duration 60 -Output test.csv -StartNow
```

Then re-run the setup without `-Duration` to switch to continuous logging. With
`-Duration`, the logger exits after each run, and the task starts a new run within
`-RestartMinutes`.

To remove the task:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_routine_logging.ps1 -Uninstall
```

Because the logger runs without a window, check `humidity_logger.log` to see whether it is
working. You can also look for new rows in MySQL or the CSV, or check Task Scheduler
(*Last Run Result* `0x0` means a clean exit; `0x41301` means it is running).

## Notes

- Channels are found by name ("Relative Humidity", "CO₂ Gas", "Temperature"), not fixed
  channel numbers.
- The first row of a run can average a couple of extra samples left over from the warm-up.
- CSV files, log files and `db_config.ini` are ignored by git, so measurement data and the
  MySQL password stay on the logging PC.
