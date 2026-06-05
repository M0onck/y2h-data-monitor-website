# -*- coding: utf-8 -*-
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import APIKeyCookie
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
import sqlite3
import math
import os
import uvicorn
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta

# 自动加载 .env 文件中的环境变量
from dotenv import load_dotenv
load_dotenv()

app = FastAPI(title="Y2H Cloud Scientific Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")

DB_FILE = "y2h_cloud_scientific.db"
DASHBOARD_PASSWORD = "8023qwer"

# ==========================================
# 1. 经典时空转换数学公式 (WGS84 <-> GCJ02 <-> BD09)
# ==========================================
X_PI = math.pi * 3000.0 / 180.0
PI = math.pi
A = 6378245.0
EE = 0.00669342162296594323

def transform_lat(lon: float, lat: float) -> float:
    ret = -100.0 + 2.0 * lon + 3.0 * lat + 0.2 * lat * lat + 0.1 * lon * lat + 0.2 * math.sqrt(abs(lon))
    ret += (20.0 * math.sin(6.0 * lon * PI) + 20.0 * math.sin(2.0 * lon * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * PI) + 40.0 * math.sin(lat / 3.0 * PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * PI) + 320 * math.sin(lat * PI / 30.0)) * 2.0 / 3.0
    return ret

def transform_lon(lon: float, lat: float) -> float:
    ret = 300.0 + lon + 2.0 * lat + 0.1 * lon * lon + 0.1 * lon * lat + 0.1 * math.sqrt(abs(lon))
    ret += (20.0 * math.sin(6.0 * lon * PI) + 20.0 * math.sin(2.0 * lon * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lon * PI) + 40.0 * math.sin(lon / 3.0 * PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lon / 12.0 * PI) + 300.0 * math.sin(lon / 30.0 * PI)) * 2.0 / 3.0
    return ret

def wgs84_to_bd09(lon: float, lat: float) -> tuple:
    if not (73.66 < lon < 135.05 and 3.86 < lat < 53.55): return lon, lat
    dlat = transform_lat(lon - 105.0, lat - 35.0)
    dlon = transform_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * PI
    magic = math.sin(radlat)
    magic = 1 - EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrt_magic) * PI)
    dlon = (dlon * 180.0) / (A / sqrt_magic * math.cos(radlat) * PI)
    glat = lat + dlat
    glon = lon + dlon
    z = math.sqrt(glon * glon + glat * glat) + 0.00002 * math.sin(glat * X_PI)
    theta = math.atan2(glat, glon) + 0.000003 * math.cos(glon * X_PI)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006

def bd09_to_wgs84(bd_lon: float, bd_lat: float) -> tuple:
    x = bd_lon - 0.0065
    y = bd_lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * X_PI)
    gcj_lon = z * math.cos(theta)
    gcj_lat = z * math.sin(theta)
    
    dlat = transform_lat(gcj_lon - 105.0, gcj_lat - 35.0)
    dlon = transform_lon(gcj_lon - 105.0, gcj_lat - 35.0)
    radlat = gcj_lat / 180.0 * PI
    magic = math.sin(radlat)
    magic = 1 - EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrt_magic) * PI)
    dlon = (dlon * 180.0) / (A / sqrt_magic * math.cos(radlat) * PI)
    mglat = gcj_lat + dlat
    mglon = gcj_lon + dlon
    return gcj_lon * 2 - mglon, gcj_lat * 2 - mglat

def spatial_cell_id(lat: float, lon: float, cell_m: float = 50.0) -> str:
    if lat is None or lon is None: return ""
    lat_m = lat * 111320.0
    lon_m = lon * 111320.0 * max(0.01, math.cos(math.radians(lat)))
    return f"{int(math.floor(lon_m / cell_m))}_{int(math.floor(lat_m / cell_m))}"

# ==========================================
# 2. 数据库与模型
# ==========================================
class SensorUploadData(BaseModel):
    timestamp: str
    pm25: Optional[float] = None
    pm10: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    speed: Optional[float] = None
    temp: Optional[float] = None
    rh: Optional[float] = None
    voc: Optional[float] = None
    co2: Optional[float] = None
    satellites: Optional[int] = None     
    fix_quality: Optional[int] = None    
    device_id: str
    gps_state: str = "locating"
    hdop: float = 99.9
    snr: float = 0.0

class EdgeSnapshotData(BaseModel):
    device_id: str
    status: str
    session_id: Optional[str] = None
    timestamp: float
    ground_temp: Any
    veh_count: int
    ldv_count: int
    hdv_count: int
    temp: Any         
    humidity: Any
    wind_speed: Any
    wind_dir: Any
    pm25: Any
    pm10: Any

class StationLocationUpdate(BaseModel):
    device_id: str
    latitude: float
    longitude: float

class AiQuery(BaseModel):
    question: str
    hours: Optional[float] = 2.0
    use_llm: bool = True

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS mobile_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME,
            pm25 REAL,
            pm10 REAL,
            latitude REAL,
            longitude REAL,
            speed REAL,
            temp REAL,
            rh REAL,
            voc REAL,
            co2 REAL,
            satellites INTEGER,
            fix_quality INTEGER,
            gps_state TEXT,
            hdop REAL,
            snr REAL,
            device_id TEXT
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS stationary_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
            pm25 REAL, pm10 REAL, temp REAL, rh REAL,
            wind_speed REAL, wind_dir REAL,
            latitude REAL, longitude REAL, device_id TEXT
        )
    ''')
    
    c.execute("SELECT COUNT(*) FROM stationary_data")
    if c.fetchone()[0] == 0:
        c.execute('''
            INSERT INTO stationary_data 
            (timestamp, pm25, pm10, temp, rh, wind_speed, wind_dir, latitude, longitude, device_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), None, None, None, None, None, None, 32.060255, 118.796877, "EDGE_NODE_01"))

    c.execute('''
        CREATE TABLE IF NOT EXISTS edge_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT,
            status TEXT,
            session_id TEXT,
            timestamp REAL,
            ground_temp TEXT,
            veh_count INTEGER,
            ldv_count INTEGER,
            hdv_count INTEGER,
            temp TEXT,
            humidity TEXT,
            wind_speed TEXT,
            wind_dir TEXT,
            pm25 TEXT,
            pm10 TEXT,
            receive_time DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    c.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON mobile_data(timestamp)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_device ON mobile_data(device_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_stat_time ON stationary_data(timestamp)')
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 3. AI/RAG 风险研判助手
# ==========================================
RAG_KNOWLEDGE_BASE = [
    {
        "id": "traffic_pm_control",
        "title": "交通源颗粒物与道路扬尘管控",
        "keywords": ["pm25", "pm2.5", "pm10", "颗粒物", "扬尘", "车流", "重型车", "hdv", "怠速", "拥堵"],
        "content": "道路颗粒物风险通常与车流密度、重型车比例、怠速排队、路面积尘和低风速扩散条件有关。短时管理可优先采取交通疏导、减少路边停靠怠速、湿扫保洁、重点路段临时巡查和重复走航复测。"
    },
    {
        "id": "mobile_station_alignment",
        "title": "走航-定点协同时空对齐",
        "keywords": ["走航", "固定点", "定点", "校准", "对齐", "gps", "滞后", "漂移", "时间"],
        "content": "走航数据适合发现空间差异，固定点数据适合提供连续基准。经过固定点附近的走航数据可用于估计传感器响应滞后和 GPS 漂移，建议把对齐结果作为风险可信度说明，而不是只看单次峰值。"
    },
    {
        "id": "heat_exposure",
        "title": "热暴露与户外活动风险",
        "keywords": ["热", "高温", "温度", "湿度", "暴露", "户外", "骑行", "外卖"],
        "content": "热风险与温度、湿度、太阳辐射、风速和暴露时间共同相关。短时建议包括避开高温时段、增加阴凉休息点、补水提醒、减少连续骑行暴露，并结合地表温度和人流活动强度判断重点区域。"
    },
    {
        "id": "co2_voc_warning",
        "title": "CO2/VOC 异常与通风排查",
        "keywords": ["co2", "二氧化碳", "voc", "异味", "通风", "室内", "地下", "封闭"],
        "content": "CO2 和 VOC 的短时异常常用于提示通风不足、局部排放或人员聚集。建议先核查传感器状态和通风条件，再结合固定点或复测数据确认是否需要调整通风、限流或排查局部污染源。"
    },
    {
        "id": "decision_language",
        "title": "辅助决策输出原则",
        "keywords": ["政策", "建议", "管理", "怎么办", "措施", "治理", "处置"],
        "content": "竞赛和管理场景中，AI 输出应定位为辅助决策建议。建议明确证据、风险等级、优先级、短期处置、中期优化和不确定性，不应把模型回答表述为执法结论或最终政策。"
    }
]

METRIC_LABELS = {
    "pm25": "PM2.5",
    "pm10": "PM10",
    "co2": "CO2",
    "voc": "VOC",
    "temp": "温度",
    "rh": "湿度",
    "wind_speed": "风速",
    "veh_count": "车辆数",
    "hdv_count": "重型车",
    "ldv_count": "轻型车",
    "ground_temp": "路面温度"
}

def safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if math.isnan(float(value)):
            return None
        return float(value)
    text = str(value).strip()
    if not text or text in {"-", "--", "None", "null", "NaN", "nan"}:
        return None
    try:
        return float(text.replace("℃", "").replace("%", "").strip())
    except ValueError:
        return None

def parse_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except (OSError, ValueError):
            return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None)
    except ValueError:
        return None

def metric_avg(values: List[float]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)

def metric_stats(values: List[float]) -> Dict[str, Any]:
    clean = [v for v in values if v is not None]
    if not clean:
        return {"count": 0, "avg": None, "max": None}
    return {
        "count": len(clean),
        "avg": round(sum(clean) / len(clean), 2),
        "max": round(max(clean), 2)
    }

def get_or_create_cell(cells: Dict[str, Dict[str, Any]], key: str, lat: float, lon: float, source: str, device_id: str) -> Dict[str, Any]:
    if key not in cells:
        bd_lon, bd_lat = wgs84_to_bd09(lon, lat)
        cells[key] = {
            "cell_id": key,
            "lat_values": [],
            "lon_values": [],
            "bd_lng": round(bd_lon, 7),
            "bd_lat": round(bd_lat, 7),
            "sources": set(),
            "devices": set(),
            "metrics": {name: [] for name in METRIC_LABELS.keys()},
            "sample_count": 0
        }
    cell = cells[key]
    cell["lat_values"].append(lat)
    cell["lon_values"].append(lon)
    cell["sources"].add(source)
    if device_id:
        cell["devices"].add(device_id)
    cell["sample_count"] += 1
    return cell

def add_metric(cell: Dict[str, Any], metric: str, value: Any):
    parsed = safe_float(value)
    if parsed is not None and metric in cell["metrics"]:
        cell["metrics"][metric].append(parsed)

def score_from_avg(stats: Dict[str, Any], metric: str, baseline: float, max_points: float) -> float:
    avg = stats.get(metric, {}).get("avg")
    if avg is None:
        return 0.0
    return min(max_points, max(0.0, avg / baseline * max_points))

def compute_risk(stats: Dict[str, Dict[str, Any]]) -> tuple:
    score = 0.0
    reasons = []

    pm25_score = score_from_avg(stats, "pm25", 75.0, 30.0)
    if pm25_score:
        score += pm25_score
        reasons.append(f"PM2.5均值 {stats['pm25']['avg']}，峰值 {stats['pm25']['max']}")

    pm10_score = score_from_avg(stats, "pm10", 150.0, 22.0)
    if pm10_score:
        score += pm10_score
        reasons.append(f"PM10均值 {stats['pm10']['avg']}，峰值 {stats['pm10']['max']}")

    co2_avg = stats.get("co2", {}).get("avg")
    if co2_avg is not None and co2_avg > 800:
        co2_score = min(12.0, (co2_avg - 800.0) / 800.0 * 12.0)
        score += co2_score
        reasons.append(f"CO2均值 {co2_avg}，提示通风或聚集风险")

    voc_avg = stats.get("voc", {}).get("avg")
    if voc_avg is not None and voc_avg > 0:
        voc_score = min(8.0, voc_avg / max(1.0, abs(voc_avg)) * 4.0)
        score += voc_score
        reasons.append(f"VOC存在有效读数，均值 {voc_avg}")

    temp_avg = stats.get("temp", {}).get("avg")
    rh_avg = stats.get("rh", {}).get("avg")
    if temp_avg is not None:
        heat_score = 0.0
        if temp_avg >= 35:
            heat_score = 12.0
        elif temp_avg >= 32:
            heat_score = 8.0
        elif temp_avg >= 30:
            heat_score = 5.0
        if rh_avg is not None and rh_avg >= 70:
            heat_score += 3.0
        if heat_score:
            score += min(15.0, heat_score)
            reasons.append(f"温湿暴露偏高：温度 {temp_avg}℃，湿度 {rh_avg if rh_avg is not None else '-'}%")

    veh_avg = stats.get("veh_count", {}).get("avg")
    hdv_avg = stats.get("hdv_count", {}).get("avg")
    if veh_avg is not None and veh_avg > 0:
        hdv_part = (hdv_avg or 0.0) * 2.5
        traffic_score = min(13.0, (veh_avg + hdv_part) / 40.0 * 13.0)
        score += traffic_score
        reasons.append(f"边缘视觉显示车辆活动：车辆均值 {veh_avg}，重型车均值 {hdv_avg if hdv_avg is not None else 0}")

    return round(min(100.0, score), 1), reasons

def risk_level(score: float) -> str:
    if score >= 75:
        return "高风险"
    if score >= 50:
        return "中高风险"
    if score >= 25:
        return "关注"
    return "低风险"

def latest_station_locations(conn: sqlite3.Connection) -> Dict[str, tuple]:
    c = conn.cursor()
    c.execute('''
        SELECT s.device_id, s.latitude, s.longitude
        FROM stationary_data s
        INNER JOIN (
            SELECT device_id, MAX(timestamp) AS max_time
            FROM stationary_data
            WHERE device_id IS NOT NULL
            GROUP BY device_id
        ) latest ON s.device_id = latest.device_id AND s.timestamp = latest.max_time
    ''')
    locations = {}
    for row in c.fetchall():
        lat = safe_float(row["latitude"])
        lon = safe_float(row["longitude"])
        if lat is not None and lon is not None:
            locations[row["device_id"]] = (lat, lon)
    return locations

def collect_recent_risk_cells(hours: float) -> tuple:
    start_dt = datetime.now() - timedelta(hours=hours)
    start_text = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    start_ts = start_dt.timestamp()
    cells: Dict[str, Dict[str, Any]] = {}
    counters = {"mobile_rows": 0, "stationary_rows": 0, "edge_rows": 0, "valid_cells": 0}

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute('''
        SELECT * FROM mobile_data
        WHERE timestamp >= ?
        ORDER BY timestamp DESC
        LIMIT 20000
    ''', (start_text,))
    for row in c.fetchall():
        counters["mobile_rows"] += 1
        lat = safe_float(row["latitude"])
        lon = safe_float(row["longitude"])
        if lat is None or lon is None:
            continue
        if abs(lat - 32.060255) < 0.00001 and abs(lon - 118.796877) < 0.00001:
            continue
        cell = get_or_create_cell(cells, spatial_cell_id(lat, lon), lat, lon, "走航", row["device_id"])
        for metric in ("pm25", "pm10", "temp", "rh", "voc", "co2"):
            add_metric(cell, metric, row[metric])

    c.execute('''
        SELECT * FROM stationary_data
        WHERE timestamp >= ?
        ORDER BY timestamp DESC
        LIMIT 10000
    ''', (start_text,))
    for row in c.fetchall():
        counters["stationary_rows"] += 1
        lat = safe_float(row["latitude"])
        lon = safe_float(row["longitude"])
        if lat is None or lon is None:
            continue
        cell = get_or_create_cell(cells, spatial_cell_id(lat, lon), lat, lon, "定点", row["device_id"])
        for metric in ("pm25", "pm10", "temp", "rh", "wind_speed"):
            add_metric(cell, metric, row[metric])

    station_locations = latest_station_locations(conn)
    c.execute('''
        SELECT * FROM edge_snapshots
        WHERE timestamp >= ?
        ORDER BY timestamp DESC
        LIMIT 10000
    ''', (start_ts,))
    for row in c.fetchall():
        counters["edge_rows"] += 1
        device_id = row["device_id"]
        if device_id not in station_locations:
            continue
        lat, lon = station_locations[device_id]
        cell = get_or_create_cell(cells, spatial_cell_id(lat, lon), lat, lon, "边缘视觉", device_id)
        metric_map = {
            "pm25": "pm25",
            "pm10": "pm10",
            "temp": "temp",
            "humidity": "rh",
            "wind_speed": "wind_speed",
            "veh_count": "veh_count",
            "hdv_count": "hdv_count",
            "ldv_count": "ldv_count",
            "ground_temp": "ground_temp"
        }
        for src_key, metric in metric_map.items():
            add_metric(cell, metric, row[src_key])

    conn.close()

    cards = []
    for cell in cells.values():
        stats = {metric: metric_stats(values) for metric, values in cell["metrics"].items()}
        measured_count = sum(stat.get("count", 0) for stat in stats.values())
        if measured_count == 0:
            continue
        score, reasons = compute_risk(stats)
        lat = metric_avg(cell["lat_values"])
        lon = metric_avg(cell["lon_values"])
        if lat is not None and lon is not None:
            bd_lon, bd_lat = wgs84_to_bd09(lon, lat)
        else:
            bd_lon, bd_lat = cell["bd_lng"], cell["bd_lat"]
        cards.append({
            "cell_id": cell["cell_id"],
            "risk_score": score,
            "risk_level": risk_level(score),
            "sample_count": cell["sample_count"],
            "sources": sorted(cell["sources"]),
            "devices": sorted(cell["devices"]),
            "wgs_lat": round(lat, 7) if lat is not None else None,
            "wgs_lon": round(lon, 7) if lon is not None else None,
            "bd_lat": round(bd_lat, 7) if bd_lat is not None else None,
            "bd_lng": round(bd_lon, 7) if bd_lon is not None else None,
            "stats": stats,
            "evidence": reasons[:5]
        })

    cards.sort(key=lambda item: item["risk_score"], reverse=True)
    counters["valid_cells"] = len(cards)
    return cards, counters

def retrieve_knowledge(question: str, top_cards: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    context = question.lower()
    for card in top_cards[:3]:
        context += " " + " ".join(card.get("evidence", [])).lower()
        context += " " + " ".join(card.get("sources", [])).lower()

    ranked = []
    for item in RAG_KNOWLEDGE_BASE:
        score = 0
        for keyword in item["keywords"]:
            if keyword.lower() in context:
                score += 2
        if score == 0 and ("建议" in question or "怎么办" in question):
            score = 1 if item["id"] == "decision_language" else 0
        ranked.append((score, item))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [item for score, item in ranked if score > 0][:3] or RAG_KNOWLEDGE_BASE[:3]

def format_stat(stats: Dict[str, Dict[str, Any]], metric: str, unit: str = "") -> Optional[str]:
    stat = stats.get(metric, {})
    if stat.get("count", 0) == 0:
        return None
    label = METRIC_LABELS.get(metric, metric)
    return f"{label}均值{stat['avg']}{unit}、峰值{stat['max']}{unit}"

def build_policy_suggestions(top_card: Optional[Dict[str, Any]]) -> List[str]:
    if not top_card:
        return ["先确认设备在线状态和采样时间窗，再进行实地复测。"]
    stats = top_card["stats"]
    suggestions = []
    pm25 = stats.get("pm25", {}).get("avg")
    pm10 = stats.get("pm10", {}).get("avg")
    veh = stats.get("veh_count", {}).get("avg")
    hdv = stats.get("hdv_count", {}).get("avg")
    temp = stats.get("temp", {}).get("avg")
    rh = stats.get("rh", {}).get("avg")
    co2 = stats.get("co2", {}).get("avg")
    voc = stats.get("voc", {}).get("avg")

    if pm25 or pm10 or veh:
        suggestions.append("短期优先对该路段进行交通疏导、减少路边停靠怠速，并安排一次高峰期重复走航复测。")
    if pm10 and pm10 >= 100:
        suggestions.append("若现场存在扬尘或路面积尘，建议增加湿扫保洁频次，并记录保洁前后的 PM10 变化。")
    if veh and (hdv or 0) > 0:
        suggestions.append("若重型车或高排放车辆集中，建议把边缘视觉截图与颗粒物峰值对齐，形成交通源证据链。")
    if temp and temp >= 30:
        suggestions.append("对外卖骑手、学生步行通道等暴露人群，建议增加遮阴、补水提醒和错峰通行提示。")
    if co2 and co2 > 1000:
        suggestions.append("CO2 偏高时先排查通风条件和局部人员聚集，再判断是否需要限流或强化通风。")
    if voc and voc > 0:
        suggestions.append("VOC 有效读数需要结合风向、异味巡查和复测确认，避免把单点瞬时值直接作为污染源结论。")
    if rh and rh >= 70 and temp and temp >= 30:
        suggestions.append("高温高湿叠加时，应把热暴露风险与颗粒物风险分开标注，便于现场管理分级处置。")
    suggestions.append("所有建议应作为辅助研判结果，最终处置需结合现场巡查和人工复核。")
    return suggestions[:5]

def build_local_ai_answer(question: str, hours: float, cards: List[Dict[str, Any]], knowledge: List[Dict[str, str]], counters: Dict[str, int]) -> str:
    if not cards:
        return (
            f"结论：近 {hours:g} 小时内没有足够的有效空间数据用于风险排序。\n"
            f"数据检查：走航 {counters['mobile_rows']} 条、定点 {counters['stationary_rows']} 条、边缘快照 {counters['edge_rows']} 条，但缺少可用坐标或有效污染物读数。\n"
            "建议：先确认设备在线、GPS 定位和固定站坐标，再进行一次覆盖重点路段的走航采样。"
        )

    top = cards[0]
    location = f"网格 {top['cell_id']}"
    if top.get("bd_lng") and top.get("bd_lat"):
        location += f"（百度坐标约 {top['bd_lng']}, {top['bd_lat']}）"

    evidence_parts = []
    for metric, unit in [("pm25", " μg/m³"), ("pm10", " μg/m³"), ("co2", " ppm"), ("voc", ""), ("temp", "℃"), ("rh", "%"), ("veh_count", ""), ("hdv_count", "")]:
        text = format_stat(top["stats"], metric, unit)
        if text:
            evidence_parts.append(text)

    lines = [
        f"结论：近 {hours:g} 小时内，{location} 的综合风险最高，风险分 {top['risk_score']}/100，等级为{top['risk_level']}。",
        f"证据：该区域来自{'、'.join(top['sources'])}数据，样本数 {top['sample_count']}，设备 {', '.join(top['devices']) or '未知'}。"
    ]
    if evidence_parts:
        lines.append("关键指标：" + "；".join(evidence_parts[:6]) + "。")
    if top["evidence"]:
        lines.append("风险解释：" + "；".join(top["evidence"]) + "。")

    suggestions = build_policy_suggestions(top)
    lines.append("建议措施：")
    for idx, suggestion in enumerate(suggestions, start=1):
        lines.append(f"{idx}. {suggestion}")

    if knowledge:
        lines.append("检索依据：" + "；".join([f"{item['title']}" for item in knowledge]) + "。")
    lines.append("不确定性：当前结果用于竞赛演示和辅助研判，短时峰值需要结合传感器校准、GPS 漂移、风向风速和现场巡查复核。")
    return "\n".join(lines)

def execute_readonly_sql(sql: str) -> str:
    """供大模型调用的工具函数：安全地执行只读 SQL 查询"""
    # 1. 基础的防注入与防篡改拦截
    forbidden_keywords = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE"]
    if any(kw in sql.upper() for kw in forbidden_keywords):
        return "Error: 权限拒绝。该工具只能执行 SELECT 语句获取数据。"
        
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        # 2. SQLite 底层级别限制只读 (彻底杜绝大模型误删数据)
        c.execute("PRAGMA query_only = ON;")
        
        c.execute(sql)
        # 3. 限制最大返回行数，防止几十万条数据瞬间撑爆大模型的 Token 上限
        rows = c.fetchmany(50) 
        cols = [desc[0] for desc in c.description] if c.description else []
        conn.close()
        
        if not rows:
            return "查询成功，但未找到匹配的数据行。"
            
        # 4. 将查询结果拼装成紧凑的 JSON 字符串返回给大模型
        res = [dict(zip(cols, row)) for row in rows]
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:
        return f"SQL执行失败，请检查语法: {str(e)}"

def call_optional_llm(question: str, hours: float, cards: List[Dict[str, Any]], knowledge: List[Dict[str, str]], fallback_answer: str, counters: Dict[str, int] = None) -> Optional[str]:
    api_url = os.getenv("Y2H_LLM_API_URL", "").strip()
    api_key = os.getenv("Y2H_LLM_API_KEY", "").strip()
    model = os.getenv("Y2H_LLM_MODEL", "").strip()
    if not api_url or not api_key or not model:
        return None
        
    if counters is None: counters = {}

    # 构造基础上下文
    prompt_text = f"【用户问题】\n{question}\n\n"
    prompt_text += f"【系统已为您预聚合的高风险网格摘要 (供宏观研判参考)】\n系统已扫描过去 {hours} 小时内的 {counters.get('mobile_rows', 0)+counters.get('stationary_rows', 0)} 条记录，提取出 {len(cards)} 个高风险网格。\n"
    if cards:
        for i, card in enumerate(cards[:3]):
            prompt_text += f"- 网格 {card['cell_id']} (风险 {card['risk_score']}): 关键证据为 {'; '.join(card['evidence'])}\n"
            
    # 【核心】：向大模型注册我们刚才写的数据库查询工具
    tools = [{
        "type": "function",
        "function": {
            "name": "query_sqlite_db",
            "description": "【关键能力】当用户的提问涉及特定设备的具体数值（如CO2最低值、最高温）、原始数据检索，且上方的网格摘要无法回答时，必须调用此工具直接查询 SQLite 数据库。表结构：1. mobile_data(timestamp, pm25, pm10, latitude, longitude, speed, temp, rh, voc, co2, device_id); 2. stationary_data(timestamp, pm25, pm10, temp, rh, wind_speed, wind_dir, device_id); 3. edge_snapshots(timestamp, veh_count, ldv_count, hdv_count, device_id)",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql_query": {
                        "type": "string", 
                        "description": "合法的 SQLite SELECT 语句。示例：SELECT MIN(co2) FROM mobile_data WHERE device_id='EDGE_NODE_01' AND timestamp >= datetime('now', '-2 hours')"
                    }
                },
                "required": ["sql_query"]
            }
        }
    }]

    messages = [
        {"role": "system", "content": "你是 Y2H 城市微环境风险研判专家。你可以基于提供的网格摘要回答宏观问题。如果遇到微观的具体数据查询请求，请主动使用 query_sqlite_db 工具执行 SQL 查库，并用查询到的事实数据回答用户。"},
        {"role": "user", "content": prompt_text}
    ]

    def _post_api(msgs):
        payload = {"model": model, "temperature": 0.2, "messages": msgs, "tools": tools, "tool_choice": "auto"}
        req = urllib.request.Request(api_url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}, method="POST")
        with urllib.request.urlopen(req, timeout=float(os.getenv("Y2H_LLM_TIMEOUT", "60"))) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        # 第一轮对话：大模型决定是直接回答，还是调用查库工具
        result = _post_api(messages)
        response_msg = result["choices"][0]["message"]
        
        # 如果大模型决定调用工具 (生成了 SQL 语句)
        if response_msg.get("tool_calls"):
            messages.append(response_msg) # 把大模型的工具请求加入上下文
            
            for tool_call in response_msg["tool_calls"]:
                if tool_call["function"]["name"] == "query_sqlite_db":
                    # 解析大模型写出的 SQL 并执行本地函数
                    args = json.loads(tool_call["function"]["arguments"])
                    sql_query = args.get("sql_query", "")
                    
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🤖 AI 触发自主查库: {sql_query}")
                    db_result = execute_readonly_sql(sql_query)
                    
                    # 将查库结果喂回给大模型
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": tool_call["function"]["name"],
                        "content": db_result
                    })
            
            # 第二轮对话：大模型拿到数据库结果，整理出最终的自然语言回答
            final_result = _post_api(messages)
            return final_result["choices"][0]["message"]["content"].strip()
            
        else:
            # 如果大模型认为无需查库（如宏观分析），直接返回结果
            return response_msg["content"].strip()

    except Exception as exc:
        print(f"[Y2H-RAG] LLM 智能体调用异常: {exc}")
        return fallback_answer

# ==========================================
# 4. 路由 API
# ==========================================
cookie_scheme = APIKeyCookie(name="y2h_cloud_session", auto_error=False)

def check_login(session: Optional[str] = Depends(cookie_scheme)):
    if not session or session != "authenticated_session_token_Y2H":
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/login"})
    return True

@app.get("/login", response_class=HTMLResponse)
def get_login_page():
    return """
    <!DOCTYPE html><html><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Y2H 远程系统登录</title>
    <style>body{font-family:system-ui;background:#0f172a;height:100vh;display:flex;align-items:center;justify-content:center;margin:0}.card{background:#1e293b;padding:32px;border-radius:20px;box-shadow:0 10px 40px rgba(0,0,0,0.5);width:320px;color:#f8fafc}input,button{width:100%;box-sizing:border-box;padding:12px;margin:10px 0;border-radius:12px;border:1px solid #475569;background:#334155;color:white;font-size:14px}button{background:#2563eb;color:white;border:0;cursor:pointer;font-weight:bold}</style></head>
    <body><div class="card"><h2 style="margin-top:0">Y2H 云登录</h2><form method="POST" action="/login"><input name="password" type="password" placeholder="访问密码"><button type="submit">安全进入</button></form></div></body></html>
    """

@app.post("/login")
async def do_login(request: Request):
    form = await request.form()
    if form.get("password") == DASHBOARD_PASSWORD:
        resp = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        resp.set_cookie(key="y2h_cloud_session", value="authenticated_session_token_Y2H", httponly=True)
        return resp
    return HTMLResponse('<script>alert("密码错误"); window.location.href="/login";</script>')

@app.get("/logout")
def do_logout():
    resp = RedirectResponse(url="/login")
    resp.delete_cookie(key="y2h_cloud_session")
    return resp

@app.get("/", response_class=HTMLResponse)
def serve_dashboard(auth: bool = Depends(check_login)):
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/devices")
def get_devices(auth: bool = Depends(check_login)):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    devices = []
    now = datetime.now()
    now_ts = now.timestamp()
    
    # === 1. 移动设备 ===
    # 【修改】：增加了对最新真实有效经纬度的子查询提取
    c.execute('''
        SELECT 
            m1.device_id, 
            MAX(m1.timestamp) as last_seen, 
            COUNT(m1.id) as total_points,
            (SELECT gps_state FROM mobile_data m2 WHERE m2.device_id = m1.device_id ORDER BY timestamp DESC LIMIT 1) as latest_gps_state,
            (SELECT latitude FROM mobile_data m2 WHERE m2.device_id = m1.device_id AND latitude IS NOT NULL AND ABS(latitude - 32.060255) > 0.00001 ORDER BY timestamp DESC LIMIT 1) as lat,
            (SELECT longitude FROM mobile_data m2 WHERE m2.device_id = m1.device_id AND longitude IS NOT NULL AND ABS(longitude - 118.796877) > 0.00001 ORDER BY timestamp DESC LIMIT 1) as lon
        FROM mobile_data m1
        WHERE m1.device_id IS NOT NULL 
        GROUP BY m1.device_id 
        ORDER BY last_seen DESC
    ''')
    
    for r in c.fetchall():
        dt_seen = datetime.strptime(r["last_seen"], "%Y-%m-%d %H:%M:%S") if r["last_seen"] else datetime.min
        
        if (now - dt_seen).total_seconds() > 300:
            status = "offline"
        elif r["latest_gps_state"] == "locating":
            status = "locating"
        else:
            status = "online"
            
        # 转换为百度坐标系供前端直接打点
        lat, lon = r["lat"], r["lon"]
        bd_lon, bd_lat = None, None
        if lat is not None and lon is not None:
            bd_lon, bd_lat = wgs84_to_bd09(lon, lat)
            
        devices.append({
            "id": r["device_id"], "type": "mobile", "last_seen": r["last_seen"], 
            "total_points": r["total_points"], "status": status,
            "lng": round(bd_lon, 7) if bd_lon else None, "lat": round(bd_lat, 7) if bd_lat else None
        })
        
    # === 2. 查阅边缘快照心跳 ===
    edge_last_seen_map = {}
    try:
        c.execute("SELECT device_id, MAX(timestamp) as edge_last_seen FROM edge_snapshots GROUP BY device_id")
        edge_last_seen_map = {row["device_id"]: row["edge_last_seen"] for row in c.fetchall()}
    except sqlite3.OperationalError:
        pass 
        
    # === 3. 定点设备 ===
    # 【修改】：同样增加对定点设备坐标的提取
    c.execute('''
        SELECT 
            s1.device_id, 
            MAX(s1.timestamp) as last_seen, 
            COUNT(s1.id) as total_points,
            (SELECT latitude FROM stationary_data s2 WHERE s2.device_id = s1.device_id ORDER BY timestamp DESC LIMIT 1) as lat,
            (SELECT longitude FROM stationary_data s2 WHERE s2.device_id = s1.device_id ORDER BY timestamp DESC LIMIT 1) as lon
        FROM stationary_data s1 
        WHERE s1.device_id IS NOT NULL 
        GROUP BY s1.device_id 
        ORDER BY last_seen DESC
    ''')
    
    for r in c.fetchall():
        device_id = r["device_id"]
        dt = datetime.strptime(r["last_seen"], "%Y-%m-%d %H:%M:%S") if r["last_seen"] else datetime.min
        is_online = (now - dt).total_seconds() <= 300
        
        if device_id in edge_last_seen_map:
            edge_ts = edge_last_seen_map[device_id]
            if edge_ts:
                try:
                    if (now_ts - float(edge_ts)) <= 300: is_online = True
                except (ValueError, TypeError): pass
                
        lat, lon = r["lat"], r["lon"]
        bd_lon, bd_lat = None, None
        if lat is not None and lon is not None:
            bd_lon, bd_lat = wgs84_to_bd09(lon, lat)
                    
        devices.append({
            "id": device_id, "type": "stationary", "last_seen": r["last_seen"], 
            "total_points": r["total_points"], "status": "online" if is_online else "offline",
            "lng": round(bd_lon, 7) if bd_lon else None, "lat": round(bd_lat, 7) if bd_lat else None
        })
        
    conn.close()
    return {"ok": True, "devices": devices}

@app.get("/api")
def get_map_data(date: str = "", start: str = "", end: str = "", device_id: str = "", device_type: str = "mobile", auth: bool = Depends(check_login)):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    table_name = "mobile_data" if device_type == "mobile" else "stationary_data"
    query = f"SELECT * FROM {table_name} WHERE device_id = ?"
    params = [device_id]
    if date:
        query += " AND timestamp LIKE ?"
        params.append(f"{date}%")
    query += " ORDER BY timestamp ASC LIMIT 8000"
    
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()

    points = []
    dates_set = set()
    
    # === 用于轨迹坐标的 Data Hold（数据保持） ===
    last_valid_lat = None
    last_valid_lon = None
    
    for row in rows:
        row_dict = dict(row)
        
        lat = row_dict.get("latitude")
        lon = row_dict.get("longitude")
        
        # 1. 容错：如果数据库里该点完全没有经纬度字段（或为 0），则无法绘制，直接跳过
        if not lat or not lon:
            continue
            
        # 2. 甄别占位符飞线
        is_invalid_pos = False
        if device_type == "mobile":
            # 去掉了对 fix_quality == 0 的严格拦截，防止新设备被误杀
            # 只拦截特定的默认占位坐标 (32.060255, 118.796877)
            if abs(lat - 32.060255) < 0.00001 and abs(lon - 118.796877) < 0.00001:
                is_invalid_pos = True
                
        # 3. 坐标记忆与回退
        if not is_invalid_pos:
            last_valid_lat = lat
            last_valid_lon = lon
            final_lat = lat
            final_lon = lon
        else:
            if last_valid_lat is not None and last_valid_lon is not None:
                # 运行中信号短暂丢失，回退到历史最后一次真实坐标
                final_lat = last_valid_lat
                final_lon = last_valid_lon
            else:
                # 设备刚开机处于“定位中”，完全剥离默认坐标
                final_lat = None
                final_lon = None
                
        if row_dict.get("timestamp"): 
            dates_set.add(row_dict["timestamp"].split(" ")[0])
            
        # 4. 只有真实坐标才进行转换，否则保持为 None
        bd_lon, bd_lat = None, None
        if final_lat is not None and final_lon is not None:
            bd_lon, bd_lat = wgs84_to_bd09(final_lon, final_lat)
        
        pt = {
            "time": row_dict.get("timestamp"), 
            "lng": round(bd_lon, 7) if bd_lon is not None else None, 
            "lat": round(bd_lat, 7) if bd_lat is not None else None,
            "pm25": row_dict.get("pm25"), 
            "pm10": row_dict.get("pm10"), 
            "temp": row_dict.get("temp"), 
            "rh": row_dict.get("rh")
        }
        
        if device_type == "mobile":
            pt.update({
                "voc": row_dict.get("voc"), 
                "co2": row_dict.get("co2"), 
                "speed": row_dict.get("speed"), 
                "satellites": row_dict.get("satellites", 0), 
                "fix_quality": row_dict.get("fix_quality", 0),
                "hdop": row_dict.get("hdop", 99.9),
                "snr": row_dict.get("snr", 0.0),
                "gps_state": row_dict.get("gps_state", "locating")
            })
        else:
            pt.update({
                "wind_speed": row_dict.get("wind_speed"), 
                "wind_dir": row_dict.get("wind_dir")
            })
        points.append(pt)
        
    return {"ok": True, "dates": sorted(list(dates_set)), "returned_count": len(points), "points": points}

@app.post("/api/ai/query")
def query_ai_advisor(data: AiQuery, auth: bool = Depends(check_login)):
    question = (data.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    hours = data.hours if data.hours is not None else 2.0
    hours = max(0.25, min(float(hours), 24.0))

    cards, counters = collect_recent_risk_cells(hours)
    knowledge = retrieve_knowledge(question, cards)
    local_answer = build_local_ai_answer(question, hours, cards, knowledge, counters)
    llm_answer = call_optional_llm(question, hours, cards, knowledge, local_answer, counters) if data.use_llm else None

    return {
        "ok": True,
        "mode": "llm-rag" if llm_answer else "local-rag",
        "question": question,
        "hours": hours,
        "answer": llm_answer or local_answer,
        "risk_cards": cards[:5],
        "knowledge": [{"id": item["id"], "title": item["title"], "content": item["content"]} for item in knowledge],
        "data_counters": counters,
        "llm_enabled": bool(os.getenv("Y2H_LLM_API_URL") and os.getenv("Y2H_LLM_API_KEY") and os.getenv("Y2H_LLM_MODEL"))
    }

@app.post("/api/stationary/location")
def update_station_location(data: StationLocationUpdate, auth: bool = Depends(check_login)):
    wgs_lon, wgs_lat = bd09_to_wgs84(data.longitude, data.latitude)
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE stationary_data SET latitude=?, longitude=? WHERE device_id=?", (round(wgs_lat, 6), round(wgs_lon, 6), data.device_id))
    conn.commit()
    conn.close()
    return {"status": "success", "wgs_lat": wgs_lat, "wgs_lon": wgs_lon}

@app.post("/api/upload")
def upload_data(data: SensorUploadData):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''INSERT INTO mobile_data 
                 (timestamp, pm25, pm10, latitude, longitude, speed, temp, rh, voc, co2, satellites, fix_quality, gps_state, hdop, snr, device_id)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
              (data.timestamp, data.pm25, data.pm10, data.latitude, data.longitude, data.speed, data.temp, data.rh, data.voc, data.co2, data.satellites, data.fix_quality, data.gps_state, data.hdop, data.snr, data.device_id))
    conn.commit()
    conn.close()
    return {"status": "success"}

# 接收边缘端状态和对齐快照的专用接口
@app.post("/api/edge/snapshot")
def upload_edge_snapshot(data: EdgeSnapshotData):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # 将可能包含 "--" 占位符的混合类型强制转为字符串存入 SQLite
    c.execute('''INSERT INTO edge_snapshots 
                 (device_id, status, session_id, timestamp, ground_temp, veh_count, ldv_count, hdv_count, 
                  temp, humidity, wind_speed, wind_dir, pm25, pm10)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
              (data.device_id, data.status, data.session_id, data.timestamp, str(data.ground_temp),
               data.veh_count, data.ldv_count, data.hdv_count, 
               str(data.temp), str(data.humidity), str(data.wind_speed), 
               str(data.wind_dir), str(data.pm25), str(data.pm10)))
               
    conn.commit()
    conn.close()
    
    # 打印日志方便在服务器终端确认数据连通性
    print(f"[Edge Sync] 收到设备 [{data.device_id}] 同步状态: {data.status}, 车辆数: {data.veh_count}")
    return {"status": "success", "message": "Snapshot saved"}

# 供大屏前端读取某台设备的最新状态和快照
@app.get("/api/edge/snapshot/latest")
def get_latest_edge_snapshot(device_id: str, auth: bool = Depends(check_login)):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    # 获取该设备时间戳最新的一条数据
    c.execute('''
        SELECT * FROM edge_snapshots 
        WHERE device_id = ? 
        ORDER BY timestamp DESC LIMIT 1
    ''', (device_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {"ok": True, "data": dict(row)}
    else:
        return {"ok": False, "message": "暂无该设备的边缘快照数据"}

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, log_level="info", reload=False)
