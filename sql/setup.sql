-- Creates the database and table that humidity_logger.py writes to.
-- Safe to run more than once. Run as an admin account (e.g. root):
--
--   mysql -u root -p -e "source sql/setup.sql"
--
-- (PowerShell has no "<" redirection, hence "source".)
--
-- The logger's own MySQL account is created separately (see README), so that
-- no password is stored in this file.

CREATE DATABASE IF NOT EXISTS vernier_godirect
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE vernier_godirect;

CREATE TABLE IF NOT EXISTS gdx_co2_readings (
    id            BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
    recorded_at   DATETIME         NOT NULL COMMENT 'Local time at the end of the averaging window',
    device        VARCHAR(64)      NOT NULL COMMENT 'Sensor name and serial, e.g. GDX-CO2 0Q5005V3',
    host          VARCHAR(64)      NOT NULL COMMENT 'Computer the logger ran on',
    interval_s    INT UNSIGNED     NOT NULL COMMENT 'Length of the averaging window in seconds',
    samples       SMALLINT UNSIGNED NOT NULL COMMENT 'Readings averaged into this row',
    humidity_pct  DECIMAL(5,2)     NULL     COMMENT 'Relative humidity, %',
    co2_ppm       DECIMAL(8,2)     NULL     COMMENT 'CO2 concentration, ppm',
    temperature_c DECIMAL(5,2)     NULL     COMMENT 'Temperature, degC',
    PRIMARY KEY (id),
    -- Lets the logger safely retry an insert without creating duplicate rows.
    UNIQUE KEY uq_device_time (device, recorded_at),
    KEY idx_recorded_at (recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
