# -*- coding: utf-8 -*-
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import APIKeyCookie
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
import math
import os
import uvicorn
from datetime import datetime
from typing import Optional, List, Any

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

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS mobile_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
            pm25 REAL, pm10 REAL, latitude REAL, longitude REAL,
            speed REAL, temp REAL, rh REAL, voc REAL, co2 REAL,
            satellites INTEGER DEFAULT 0, fix_quality INTEGER DEFAULT 0, device_id TEXT
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
# 3. 路由 API
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
    now_ts = now.timestamp() # 获取浮点时间戳用于和边缘端比较
    
    # === 1. 移动设备 ===
    c.execute('''
        SELECT 
            device_id, 
            MAX(timestamp) as last_seen, 
            COUNT(id) as total_points,
            MAX(CASE WHEN latitude IS NOT NULL AND ABS(latitude - 32.060255) > 0.00001 THEN timestamp ELSE NULL END) as last_valid_gps_time
        FROM mobile_data 
        WHERE device_id IS NOT NULL 
        GROUP BY device_id 
        ORDER BY last_seen DESC
    ''')
    
    for r in c.fetchall():
        dt_seen = datetime.strptime(r["last_seen"], "%Y-%m-%d %H:%M:%S") if r["last_seen"] else datetime.min
        dt_gps = datetime.strptime(r["last_valid_gps_time"], "%Y-%m-%d %H:%M:%S") if r["last_valid_gps_time"] else datetime.min
        
        sec_since_seen = (now - dt_seen).total_seconds()
        sec_since_gps = (now - dt_gps).total_seconds()
        
        # 5分钟无心跳则离线；30秒无有效GPS则定位中；否则在线。
        if sec_since_seen > 300:
            status = "offline"
        elif sec_since_gps > 30:
            status = "locating"
        else:
            status = "online"
            
        devices.append({
            "id": r["device_id"], 
            "type": "mobile", 
            "last_seen": r["last_seen"], 
            "total_points": r["total_points"], 
            "status": status
        })
        
    # === 2. 查阅边缘快照心跳 (加入防崩保护) ===
    edge_last_seen_map = {}
    try:
        # edge_snapshots 表里的 timestamp 存的是 float 
        c.execute("SELECT device_id, MAX(timestamp) as edge_last_seen FROM edge_snapshots GROUP BY device_id")
        edge_last_seen_map = {row["device_id"]: row["edge_last_seen"] for row in c.fetchall()}
    except sqlite3.OperationalError:
        pass # 如果还没建表则安静地跳过
        
    # === 3. 定点设备 (融合你的原逻辑与边缘端心跳) ===
    c.execute('SELECT device_id, MAX(timestamp) as last_seen, COUNT(id) as total_points FROM stationary_data WHERE device_id IS NOT NULL GROUP BY device_id ORDER BY last_seen DESC')
    for r in c.fetchall():
        device_id = r["device_id"]
        dt = datetime.strptime(r["last_seen"], "%Y-%m-%d %H:%M:%S") if r["last_seen"] else datetime.min
        
        # 默认按原表时间判断
        is_online = (now - dt).total_seconds() <= 300
        
        # [核心修复] 如果边缘快照表里有 300 秒内的新心跳，强制判定为在线！
        if device_id in edge_last_seen_map:
            edge_ts = edge_last_seen_map[device_id]
            if edge_ts:
                try:
                    if (now_ts - float(edge_ts)) <= 300:
                        is_online = True
                except (ValueError, TypeError):
                    pass
                    
        devices.append({
            "id": device_id, 
            "type": "stationary", 
            "last_seen": r["last_seen"], 
            "total_points": r["total_points"], 
            "status": "online" if is_online else "offline"
        })
        
    conn.close()
    
    # 完美保持原有的 JSON 结构返回
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
                "fix_quality": row_dict.get("fix_quality", 0)
            })
        else:
            pt.update({
                "wind_speed": row_dict.get("wind_speed"), 
                "wind_dir": row_dict.get("wind_dir")
            })
        points.append(pt)
        
    return {"ok": True, "dates": sorted(list(dates_set)), "returned_count": len(points), "points": points}

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
    c.execute('''INSERT INTO mobile_data (timestamp, pm25, pm10, latitude, longitude, speed, temp, rh, voc, co2, satellites, fix_quality, device_id)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
              (data.timestamp, data.pm25, data.pm10, data.latitude, data.longitude, data.speed, data.temp, data.rh, data.voc, data.co2, data.satellites, data.fix_quality, data.device_id))
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
