"""Log relative humidity (plus CO2 and temperature) from a Vernier Go Direct
CO2 Gas Sensor (GDX-CO2) to a MySQL table and/or a CSV file.

Requires:  pip install -r requirements.txt
           (godirect, which pulls in hidapi and bleak; mysql-connector-python for --mysql)

Examples:
    python humidity_logger.py                      # USB, 60 s rows to CSV, run until Ctrl+C
    python humidity_logger.py --mysql              # also insert each row into MySQL
    python humidity_logger.py --test-db            # check the MySQL settings and exit
    python humidity_logger.py --interval 300 --duration 3600
    python humidity_logger.py --ble --humidity-only
"""

import argparse
import configparser
import csv
import logging
import os
import re
import socket
import sys
import time
import unicodedata
from datetime import datetime

from godirect import GoDirect

HERE = os.path.dirname(os.path.abspath(__file__))

# Substrings matched (case-insensitively) against each sensor's description.
CHANNEL_KEYS = {
    "humidity": "humidity",
    "co2": "co2",
    "temperature": "temperature",
}

# MySQL column for each channel.
DB_COLUMNS = {
    "humidity": "humidity_pct",
    "co2": "co2_ppm",
    "temperature": "temperature_c",
}

# Rows kept in memory while MySQL is unreachable (a week of 60 s rows).
# Older rows are dropped from the queue but are still in the CSV backup.
MAX_PENDING_ROWS = 10080

log = logging.getLogger("humidity_logger")


def setup_logging(log_file):
    log.setLevel(logging.INFO)
    # pythonw.exe (used by Task Scheduler) has no console, so sys.stdout is None.
    if sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(console)
    if log_file:
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                                               "%Y-%m-%d %H:%M:%S"))
        log.addHandler(handler)


def fail(message):
    log.error(message)
    sys.exit(1)


def ascii_unit(unit):
    """Turn a unit like '°C' or '%' into a plain column-name suffix."""
    unit = unit.replace("°", "").replace("%", "pct").replace(" ", "")
    return unicodedata.normalize("NFKC", unit).encode("ascii", "ignore").decode() or "value"


def parse_args():
    p = argparse.ArgumentParser(description="Log RH/CO2/temperature from a GDX-CO2.")
    p.add_argument("--interval", type=float, default=60,
                   help="seconds averaged into each row (default 60)")
    p.add_argument("--sample-period", type=int, default=1000,
                   help="sensor sample period in ms (default 1000)")
    p.add_argument("--warmup", type=float, default=90,
                   help="seconds of readings discarded at start (default 90)")
    p.add_argument("--duration", type=float, default=None,
                   help="stop after logging this many seconds, not counting warm-up "
                        "(default: run until Ctrl+C)")
    p.add_argument("--output", default="humidity_log.csv",
                   help="CSV file to append to (default humidity_log.csv)")
    p.add_argument("--no-csv", action="store_true",
                   help="don't write a CSV file (use with --mysql)")
    p.add_argument("--mysql", action="store_true",
                   help="insert each row into the MySQL table in --db-config")
    p.add_argument("--db-config", default=os.path.join(HERE, "db_config.ini"),
                   help="MySQL settings file (default db_config.ini next to this script)")
    p.add_argument("--test-db", action="store_true",
                   help="check the MySQL connection and table, then exit")
    p.add_argument("--log-file", default=None,
                   help="also write status messages and errors to this file")
    p.add_argument("--ble", action="store_true", help="connect over Bluetooth instead of USB")
    p.add_argument("--humidity-only", action="store_true",
                   help="log only relative humidity")
    args = p.parse_args()
    if args.no_csv and not args.mysql:
        p.error("--no-csv needs --mysql, otherwise nothing would be saved")
    return args


class MySQLSink:
    """Inserts rows into MySQL, queueing them while the server is unreachable."""

    def __init__(self, config_path):
        try:
            import mysql.connector
        except ImportError:
            fail("mysql-connector-python is not installed. Run: "
                 "python -m pip install mysql-connector-python")
        self.mysql = mysql.connector

        if not os.path.exists(config_path):
            fail(f"MySQL settings file not found: {config_path}. "
                 "Copy db_config.example.ini to db_config.ini and fill it in.")
        parser = configparser.ConfigParser()
        parser.read(config_path, encoding="utf-8")
        if "mysql" not in parser:
            fail(f"{config_path} has no [mysql] section.")
        cfg = parser["mysql"]
        self.connect_args = {
            "host": cfg.get("host", "localhost"),
            "port": cfg.getint("port", 3306),
            "user": cfg.get("user", "gdx_logger"),
            "password": cfg.get("password", ""),
            "database": cfg.get("database", "vernier_godirect"),
            "connection_timeout": 10,
        }
        self.table = cfg.get("table", "gdx_co2_readings")
        if not re.fullmatch(r"[A-Za-z0-9_]+", self.table):
            fail(f"Invalid table name in {config_path}: {self.table!r}")
        self.where = (f"{self.connect_args['user']}@{self.connect_args['host']}:"
                      f"{self.connect_args['port']}/{self.connect_args['database']}.{self.table}")
        self.conn = None
        self.pending = []
        self.failing = False

    def _connect(self):
        if self.conn is None or not self.conn.is_connected():
            self.conn = self.mysql.connect(**self.connect_args)
        return self.conn

    def _disconnect(self):
        try:
            if self.conn is not None:
                self.conn.close()
        except Exception:
            pass
        self.conn = None

    def test(self):
        """Return the current row count, raising if the server or table is unusable."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM `{self.table}`")
        (count,) = cur.fetchone()
        cur.close()
        return count

    def add(self, row):
        self.pending.append(row)
        if len(self.pending) > MAX_PENDING_ROWS:
            dropped = len(self.pending) - MAX_PENDING_ROWS
            del self.pending[:dropped]
            log.warning(f"MySQL queue full; dropped {dropped} oldest row(s) (still in the CSV).")
        self.flush()

    def flush(self):
        if not self.pending:
            return
        columns = list(self.pending[0].keys())
        sql = (f"INSERT INTO `{self.table}` ({', '.join(columns)}) "
               f"VALUES ({', '.join(['%s'] * len(columns))}) "
               # A retried row that already made it in is skipped, not duplicated.
               "ON DUPLICATE KEY UPDATE id = id")
        try:
            conn = self._connect()
            cur = conn.cursor()
            cur.executemany(sql, [tuple(r[c] for c in columns) for r in self.pending])
            conn.commit()
            cur.close()
        except self.mysql.Error as e:
            self._disconnect()
            if not self.failing:
                log.warning(f"MySQL insert failed ({e}); queueing rows and retrying "
                            "every interval.")
            self.failing = True
            return
        if self.failing:
            log.info(f"MySQL reachable again; inserted {len(self.pending)} queued row(s).")
        self.failing = False
        self.pending.clear()

    def close(self):
        self.flush()
        if self.pending:
            log.warning(f"{len(self.pending)} row(s) could not be inserted into MySQL "
                        "(they are in the CSV).")
        self._disconnect()


def find_device(godirect, use_ble):
    if use_ble:
        log.info("Searching for the nearest Go Direct device over Bluetooth...")
        device = godirect.get_device(threshold=-100)
        if device is None:
            fail("No Bluetooth Go Direct device found. Is the sensor on and in range?")
        return device
    devices = godirect.list_devices()
    if not devices:
        fail("No USB Go Direct device found. Is the sensor plugged in?")
    for d in devices:
        if "CO2" in str(d.name).upper():
            return d
    return devices[0]


def pick_channels(device, wanted):
    """Map our keys ('humidity', ...) to sensor numbers by description."""
    sensors = device.list_sensors()
    found = {}
    for key in wanted:
        needle = CHANNEL_KEYS[key]
        for number, sensor in sensors.items():
            # NFKC folds the device's "CO₂" subscript to a plain "CO2".
            description = unicodedata.normalize("NFKC", sensor.sensor_description).lower()
            if needle in description:
                found[key] = number
                break
    missing = [k for k in wanted if k not in found]
    if missing:
        listing = ", ".join(f"{n}: {s.sensor_description}" for n, s in sensors.items())
        fail(f"Could not find channel(s) {missing}. Device reports: {listing}")
    return found


def main():
    args = parse_args()
    setup_logging(args.log_file)
    wanted = ["humidity"] if args.humidity_only else ["humidity", "co2", "temperature"]

    db = None
    if args.mysql or args.test_db:
        db = MySQLSink(args.db_config)
        try:
            count = db.test()
        except db.mysql.Error as e:
            fail(f"Cannot use MySQL table {db.where}: {e}")
        log.info(f"MySQL OK: {db.where} has {count} row(s).")
        if args.test_db:
            db.close()
            return

    godirect = GoDirect(use_ble=args.ble, use_usb=not args.ble)
    device = None
    out = None
    try:
        device = find_device(godirect, args.ble)
        if not device.open(auto_start=False):
            fail(f"Could not open {device.name}.")
        device_name = str(device.name)[:64]
        host = socket.gethostname()[:64]
        log.info(f"Connected to {device_name}")

        channels = pick_channels(device, wanted)
        device.enable_sensors(list(channels.values()))
        by_number = {s.sensor_number: s for s in device.get_enabled_sensors()}
        key_by_number = {n: k for k, n in channels.items()}
        units = {k: by_number[n].sensor_units for k, n in channels.items()}
        for k in wanted:
            log.info(f"  {k}: channel {channels[k]} ({units[k]})")

        if not args.no_csv:
            header = ["timestamp", "samples"] + [f"{k}_{ascii_unit(units[k])}" for k in wanted]
            new_file = not os.path.exists(args.output) or os.path.getsize(args.output) == 0
            out = open(args.output, "a", newline="", encoding="utf-8")
            writer = csv.writer(out)
            if new_file:
                writer.writerow(header)
                out.flush()

        device.start(period=args.sample_period)
        start = time.monotonic()
        warmup_end = start + args.warmup
        stop_at = None  # set once warm-up ends, so --duration is logging time
        log.info(f"Warming up for {args.warmup:g} s...")

        sums = {k: 0.0 for k in wanted}
        count = 0
        window_end = None

        while True:
            if not device.read():
                continue
            now = time.monotonic()
            values = {}
            for n, sensor in by_number.items():
                if sensor.values:
                    values[key_by_number[n]] = sensor.values[-1]
                sensor.clear()

            if now < warmup_end:
                continue
            if window_end is None:
                window_end = now + args.interval
                if args.duration:
                    stop_at = now + args.duration
                targets = []
                if out is not None:
                    targets.append(os.path.abspath(args.output))
                if db is not None:
                    targets.append(f"MySQL {db.where}")
                log.info(f"Logging to {' and '.join(targets)} every {args.interval:g} s "
                         "(Ctrl+C to stop)")

            if all(k in values for k in wanted):
                for k in wanted:
                    sums[k] += values[k]
                count += 1

            if now >= window_end:
                if count:
                    stamp = datetime.now().replace(microsecond=0)
                    averages = {k: round(sums[k] / count, 2) for k in wanted}
                    row = [stamp.isoformat(), count] + [averages[k] for k in wanted]
                    if out is not None:
                        writer.writerow(row)
                        out.flush()
                    if db is not None:
                        db_row = {"recorded_at": stamp, "device": device_name, "host": host,
                                  "interval_s": round(args.interval), "samples": count}
                        for k, column in DB_COLUMNS.items():
                            db_row[column] = averages.get(k)
                        db.add(db_row)
                    log.info(", ".join(str(x) for x in row))
                sums = {k: 0.0 for k in wanted}
                count = 0
                window_end += args.interval
                # Stop once the next window would run past the requested duration.
                if stop_at and window_end > stop_at + 1e-6:
                    log.info("Duration reached.")
                    break

            # Fallback in case rows stop being written (e.g. no samples arriving).
            if stop_at and now >= stop_at + args.interval:
                log.info("Duration reached.")
                break
    except KeyboardInterrupt:
        log.info("Stopped by user.")
    except Exception:
        log.exception("Logger stopped because of an error.")
        raise
    finally:
        if device is not None:
            try:
                device.stop()
                device.close()
            except Exception:
                pass
        godirect.quit()
        if out is not None:
            out.close()
        if db is not None:
            db.close()


if __name__ == "__main__":
    main()
