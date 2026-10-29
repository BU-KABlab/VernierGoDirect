-- Creates the table that humidity_logger.py writes to, in an existing database.
-- Safe to run more than once.
--
-- MySQL Workbench: open your server's connection, double-click the target
-- database under SCHEMAS so it is shown in bold (the default schema), then
-- open this file and execute it.
--
-- Command line:
--   mysql -h <server> -u <user> -p <database> -e "source sql/create_table.sql"
--
-- Put the same database name and table name in db_config.ini.

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Vernier GDX-CO2 readings from VernierGoDirect/humidity_logger.py';
