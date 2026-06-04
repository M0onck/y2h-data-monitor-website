# -*- coding: utf-8 -*-
import argparse
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_FILE = ROOT / "y2h_cloud_scientific.db"


def clear_demo_rows(conn):
    c = conn.cursor()
    c.execute("DELETE FROM mobile_data WHERE device_id LIKE 'DEMO_%'")
    c.execute("DELETE FROM stationary_data WHERE device_id LIKE 'DEMO_%'")
    c.execute("DELETE FROM edge_snapshots WHERE device_id LIKE 'DEMO_%'")
    conn.commit()


def insert_mobile_track(conn, now):
    c = conn.cursor()
    base_lat = 32.06155
    base_lon = 118.80045
    mild_lat = 32.05845
    mild_lon = 118.79210

    rows = []
    for i in range(72):
        minutes_ago = 110 - i * 1.5
        ts = now - timedelta(minutes=minutes_ago)
        if i < 44:
            # High-risk segment near the demo station.
            lat = base_lat + math.sin(i / 5.0) * 0.00035
            lon = base_lon + math.cos(i / 6.0) * 0.00040
            pm25 = 95 + (i % 8) * 4
            pm10 = 190 + (i % 10) * 9
            temp = 33.2 + (i % 5) * 0.35
            rh = 70 + (i % 4)
            voc = 1.1 + (i % 3) * 0.18
            co2 = 1120 + (i % 9) * 28
            speed = 10 + (i % 5) * 2
        else:
            # Moderate background segment.
            lat = mild_lat + math.sin(i / 4.0) * 0.00025
            lon = mild_lon + math.cos(i / 5.0) * 0.00030
            pm25 = 38 + (i % 6) * 3
            pm10 = 65 + (i % 8) * 5
            temp = 30.5 + (i % 4) * 0.25
            rh = 63 + (i % 5)
            voc = 0.35 + (i % 3) * 0.08
            co2 = 720 + (i % 8) * 22
            speed = 16 + (i % 4) * 3

        rows.append((
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            pm25, pm10, lat, lon, speed, temp, rh, voc, co2,
            18, 2, "fixed", 0.9, 36.0, "DEMO_MOBILE_01"
        ))

    c.executemany('''
        INSERT INTO mobile_data
        (timestamp, pm25, pm10, latitude, longitude, speed, temp, rh, voc, co2,
         satellites, fix_quality, gps_state, hdop, snr, device_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', rows)


def insert_station_and_edge(conn, now):
    c = conn.cursor()
    station_id = "DEMO_STATION_01"
    lat = 32.06155
    lon = 118.80045

    station_rows = []
    edge_rows = []
    for i in range(18):
        ts = now - timedelta(minutes=85 - i * 5)
        station_rows.append((
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            102 + (i % 5) * 4,
            210 + (i % 6) * 8,
            33.4 + (i % 4) * 0.3,
            72 + (i % 3),
            0.8 + (i % 4) * 0.12,
            135 + (i % 8) * 6,
            lat,
            lon,
            station_id
        ))
        edge_rows.append((
            station_id,
            "运行中",
            "demo-session",
            ts.timestamp(),
            str(43.0 + (i % 5) * 0.8),
            64 + (i % 7) * 3,
            55 + (i % 5) * 2,
            7 + (i % 3),
            str(33.5 + (i % 4) * 0.2),
            str(72 + (i % 3)),
            str(0.8 + (i % 4) * 0.12),
            str(135 + (i % 8) * 6),
            str(105 + (i % 5) * 5),
            str(220 + (i % 6) * 9)
        ))

    c.executemany('''
        INSERT INTO stationary_data
        (timestamp, pm25, pm10, temp, rh, wind_speed, wind_dir, latitude, longitude, device_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', station_rows)
    c.executemany('''
        INSERT INTO edge_snapshots
        (device_id, status, session_id, timestamp, ground_temp, veh_count, ldv_count, hdv_count,
         temp, humidity, wind_speed, wind_dir, pm25, pm10)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', edge_rows)


def main():
    parser = argparse.ArgumentParser(description="Seed Y2H AI advisor demo data.")
    parser.add_argument("--keep", action="store_true", help="Keep existing demo rows instead of replacing them.")
    parser.add_argument("--clear-only", action="store_true", help="Only remove DEMO_* rows and exit.")
    args = parser.parse_args()

    if not DB_FILE.exists():
        raise SystemExit(f"Database not found: {DB_FILE}")

    conn = sqlite3.connect(DB_FILE)
    try:
        if args.clear_only:
            clear_demo_rows(conn)
            print("Demo data cleared.")
            return
        if not args.keep:
            clear_demo_rows(conn)
        now = datetime.now()
        insert_mobile_track(conn, now)
        insert_station_and_edge(conn, now)
        conn.commit()
    finally:
        conn.close()

    print("Demo data inserted.")
    print("Mobile device: DEMO_MOBILE_01")
    print("Station device: DEMO_STATION_01")
    print("Ask the AI advisor: 根据过去2小时的数据，哪里环境风险最高？应该怎么办？")


if __name__ == "__main__":
    main()
