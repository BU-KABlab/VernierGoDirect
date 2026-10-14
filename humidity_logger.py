"""Log relative humidity (plus CO2 and temperature) from a Vernier Go Direct
CO2 Gas Sensor (GDX-CO2) to a CSV file.

Requires:  pip install -r requirements.txt   (godirect, which pulls in hidapi and bleak)

Examples:
    python humidity_logger.py                      # USB, 60 s rows, run until Ctrl+C
    python humidity_logger.py --interval 300 --duration 3600
    python humidity_logger.py --ble --humidity-only
"""

import argparse
import csv
import os
import sys
import time
import unicodedata
from datetime import datetime

from godirect import GoDirect

# Substrings matched (case-insensitively) against each sensor's description.
CHANNEL_KEYS = {
    "humidity": "humidity",
    "co2": "co2",
    "temperature": "temperature",
}


def ascii_unit(unit):
    """Turn a unit like '°C' or '%' into a plain column-name suffix."""
    unit = unit.replace("°", "").replace("%", "pct").replace(" ", "")
    return unicodedata.normalize("NFKC", unit).encode("ascii", "ignore").decode() or "value"


def parse_args():
    p = argparse.ArgumentParser(description="Log RH/CO2/temperature from a GDX-CO2.")
    p.add_argument("--interval", type=float, default=60,
                   help="seconds averaged into each CSV row (default 60)")
    p.add_argument("--sample-period", type=int, default=1000,
                   help="sensor sample period in ms (default 1000)")
    p.add_argument("--warmup", type=float, default=90,
                   help="seconds of readings discarded at start (default 90)")
    p.add_argument("--duration", type=float, default=None,
                   help="stop after logging this many seconds, not counting warm-up (default: run until Ctrl+C)")
    p.add_argument("--output", default="humidity_log.csv",
                   help="CSV file to append to (default humidity_log.csv)")
    p.add_argument("--ble", action="store_true", help="connect over Bluetooth instead of USB")
    p.add_argument("--humidity-only", action="store_true",
                   help="log only relative humidity")
    return p.parse_args()


def find_device(godirect, use_ble):
    if use_ble:
        print("Searching for the nearest Go Direct device over Bluetooth...")
        device = godirect.get_device(threshold=-100)
        if device is None:
            sys.exit("No Bluetooth Go Direct device found. Is the sensor on and in range?")
        return device
    devices = godirect.list_devices()
    if not devices:
        sys.exit("No USB Go Direct device found. Is the sensor plugged in?")
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
        sys.exit(f"Could not find channel(s) {missing}. Device reports: {listing}")
    return found


def main():
    args = parse_args()
    wanted = ["humidity"] if args.humidity_only else ["humidity", "co2", "temperature"]

    godirect = GoDirect(use_ble=args.ble, use_usb=not args.ble)
    device = None
    try:
        device = find_device(godirect, args.ble)
        if not device.open(auto_start=False):
            sys.exit(f"Could not open {device.name}.")
        print(f"Connected to {device.name}")

        channels = pick_channels(device, wanted)
        device.enable_sensors(list(channels.values()))
        by_number = {s.sensor_number: s for s in device.get_enabled_sensors()}
        key_by_number = {n: k for k, n in channels.items()}
        units = {k: by_number[n].sensor_units for k, n in channels.items()}
        for k in wanted:
            print(f"  {k}: channel {channels[k]} ({units[k]})")

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
        print(f"Warming up for {args.warmup:g} s...")

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
                print(f"Logging to {os.path.abspath(args.output)} every {args.interval:g} s "
                      "(Ctrl+C to stop)")

            if all(k in values for k in wanted):
                for k in wanted:
                    sums[k] += values[k]
                count += 1

            if now >= window_end:
                if count:
                    row = [datetime.now().isoformat(timespec="seconds"), count]
                    row += [round(sums[k] / count, 2) for k in wanted]
                    writer.writerow(row)
                    out.flush()
                    print(", ".join(str(x) for x in row))
                sums = {k: 0.0 for k in wanted}
                count = 0
                window_end += args.interval
                # Stop once the next window would run past the requested duration.
                if stop_at and window_end > stop_at + 1e-6:
                    print("Duration reached.")
                    break

            # Fallback in case rows stop being written (e.g. no samples arriving).
            if stop_at and now >= stop_at + args.interval:
                print("Duration reached.")
                break
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        if device is not None:
            try:
                device.stop()
                device.close()
            except Exception:
                pass
        godirect.quit()
        try:
            out.close()
        except NameError:
            pass


if __name__ == "__main__":
    main()
