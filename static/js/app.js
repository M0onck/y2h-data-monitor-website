let map = new BMap.Map('map', {enableMapClick: false});
map.centerAndZoom(new BMap.Point(118.79, 32.06), 15);
map.enableDragging();
map.enableInertialDragging();
map.enableScrollWheelZoom(true);

let overlays = [], points = [];
let currentLayer = 'track', autoRefresh = true;

// 状态机变量
let currentDeviceId = null; 
let currentDeviceType = null; 
let isPanelCollapsed = false; // 控制面板是否折叠的状态标志
const REFRESH_INTERVAL = 5000;

// ======================== 面板折叠展开机制 ========================
function togglePanel() {
    isPanelCollapsed = !isPanelCollapsed;
    const panel = document.getElementById('mainPanel');
    const fab = document.getElementById('fab');
    
    if (isPanelCollapsed) {
        panel.classList.add('collapsed');
        fab.classList.add('visible');
    } else {
        panel.classList.remove('collapsed');
        fab.classList.remove('visible');
    }
}

function clearOverlays() {
    overlays.forEach(o => map.removeOverlay(o));
    overlays = [];
}
function pt(d) { return new BMap.Point(d.lng, d.lat); }
function val(v) { return (v === null || v === undefined || v === '' || v === '--' || String(v) === 'NaN') ? '-' : v; }

function colorRamp(t) {
    t = Math.max(0, Math.min(1, t));
    if (t < .25) return '#2c7bb6';
    if (t < .5) return '#00a6ca';
    if (t < .7) return '#ffffbf';
    if (t < .85) return '#fdae61';
    return '#d7191c';
}

function normalize(vals, v) {
    let c = vals.filter(x => x !== null && x !== undefined && !isNaN(x));
    if (!c.length) return 0.5;
    let mn = Math.min(...c), mx = Math.max(...c);
    return mx === mn ? 0.5 : (v - mn) / (mx - mn);
}

function setActive(l) {
    document.querySelectorAll('button.layer').forEach(b => b.classList.toggle('active', b.dataset.layer === l));
}

// 走航气泡弹窗
function popupMobile(d) {
    let fix_text = "未定位";
    if(d.fix_quality === 1) fix_text = "单点定位";
    else if(d.fix_quality === 2) fix_text = "差分解";
    return `<div style="color:#0f172a;font-size:12px;line-height:1.6">
        <b style="font-size:14px;color:#1e3a8a">${val(d.time)}</b><br><hr style="border-top:1px solid #e2e8f0;margin:4px 0">
        <b>定位：</b>${val(d.satellites)}星 | ${fix_text} | ${val(d.speed)} km/h<br>
        <b>温度/湿度：</b>${val(d.temp)} ℃ / ${val(d.rh)} %<br>
        <b>PM2.5/10：</b>${val(d.pm25)} / ${val(d.pm10)} μg/m³<br>
        <b>VOC/CO₂：</b>${val(d.voc)} / ${val(d.co2)}
    </div>`;
}

// ======================== 设备交互与折叠逻辑 ========================
function selectDevice(id, type) {
    // 1. 点击已被选中的设备 -> 执行【收起二级面板】操作
    if(currentDeviceId === id) {
        currentDeviceId = null;
        currentDeviceType = null;
        document.getElementById('mobileDashboard').style.display = 'none';
        document.getElementById('stationaryDashboard').style.display = 'none';
        document.querySelectorAll('.device-item').forEach(el => el.classList.remove('active-device'));
        clearOverlays();
        if(stationMarker) { map.removeOverlay(stationMarker); stationMarker = null; }
        return;
    }
    
    // 2. 点击新设备 -> 执行【展开对应二级面板】操作
    currentDeviceId = id;
    currentDeviceType = type;
    
    document.querySelectorAll('.device-item').forEach(el => {
        el.classList.remove('active-device');
        if(el.dataset.id === id) el.classList.add('active-device');
    });
    
    if (type === 'mobile') {
        document.getElementById('mobileDashboard').style.display = 'block';
        document.getElementById('stationaryDashboard').style.display = 'none';
        document.getElementById('mobileDeviceName').innerText = id;
    } else {
        document.getElementById('mobileDashboard').style.display = 'none';
        document.getElementById('stationaryDashboard').style.display = 'block';
        document.getElementById('statDeviceName').innerText = id;
        isMapUnlocked = false; // 切换设备时强制锁定
        document.getElementById('lockBtn').innerText = '🔒 解锁选点';
        document.getElementById('lockBtn').style.background = '#334155';
        map.setDefaultCursor("default");
    }
    
    clearOverlays();
    if(stationMarker) { map.removeOverlay(stationMarker); stationMarker = null; }
    loadData(true);
}

// ======================== 地图选点与定点标定引擎 ========================
let isMapUnlocked = false;
let stationMarker = null;

async function toggleMapLock() {
    isMapUnlocked = !isMapUnlocked;
    const btn = document.getElementById('lockBtn');
    
    if(isMapUnlocked) {
        // 解锁状态：可以选点
        btn.innerText = '🔓 锁定并保存坐标';
        btn.style.background = '#ea580c';
        map.setDefaultCursor("crosshair");
    } else {
        // 锁定状态：将当前 Marker 坐标推上云端保存
        btn.innerText = '🔒 解锁选点';
        btn.style.background = '#334155';
        map.setDefaultCursor("default");
        
        if (stationMarker) {
            let pt = stationMarker.getPosition();
            try {
                let res = await fetch('/api/stationary/location', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        device_id: currentDeviceId,
                        longitude: pt.lng,
                        latitude: pt.lat
                    })
                });
                let ret = await res.json();
                if(ret.status === 'success') {
                    document.getElementById('statSummary').innerHTML = `<span class="badge" style="background:#10b981;color:white">站址重标定成功！</span>`;
                    setTimeout(()=> document.getElementById('statSummary').innerHTML = '', 3000);
                }
            } catch(e) {
                console.error("Save loc err", e);
            }
        }
    }
}

// 监听百度地图点击事件
map.addEventListener('click', function(e) {
    if(isMapUnlocked && currentDeviceType === 'stationary') {
        drawStationMarker(e.point);
    }
});

function drawStationMarker(pt) {
    if(stationMarker) map.removeOverlay(stationMarker);
    stationMarker = new BMap.Marker(pt);
    map.addOverlay(stationMarker);
    document.getElementById('coordText').innerText = `坐标: ${pt.lng.toFixed(5)}, ${pt.lat.toFixed(5)}`;
}

// ======================== 数据加载与渲染核心 ========================
async function fetchDevices(autoSelect = false) {
    try {
        let res = await fetch('/api/devices');
        let data = await res.json();
        
        let mobileHtml = '';
        let statHtml = '';
        
        data.devices.forEach(d => {
            let statusClass = d.status === 'online' ? 'online' : 'offline';
            let statusText = d.status === 'online' ? '在线' : '离线';
            let activeClass = (d.id === currentDeviceId) ? 'active-device' : '';
            let item = `
            <div class="device-item ${activeClass}" data-id="${d.id}" onclick="selectDevice('${d.id}', '${d.type}')">
                <div>
                    <div class="d-name"><span class="status-dot ${statusClass}"></span>${d.id}</div>
                    <div class="d-time">最后通讯: ${d.last_seen}</div>
                </div>
                <div style="text-align:right;">
                    <div class="badge" style="background:#334155; color:#f8fafc; border:0; margin-bottom:4px">${statusText}</div><br>
                    <span style="font-size:11px; color:#94a3b8">${d.total_points} pts</span>
                </div>
            </div>`;
            
            if (d.type === 'mobile') mobileHtml += item;
            else statHtml += item;
        });
        
        document.getElementById('mobileDeviceList').innerHTML = mobileHtml || '<div class="small">暂无设备</div>';
        document.getElementById('stationaryDeviceList').innerHTML = statHtml || '<div class="small">暂无固定站设备</div>';
        
    } catch(e) {}
}

async function loadData(fit = true) {
    if (!currentDeviceId) return;
    
    // 获取当前类型对应的下拉框
    let dateSelectId = currentDeviceType === 'mobile' ? 'dateSelectMobile' : 'dateSelectStat';
    let date = document.getElementById(dateSelectId).value || "";
    let start = document.getElementById('startTime').value || '00:00';
    let end = document.getElementById('endTime').value || '23:59';
        
    try {
        let res = await fetch(`/api?date=${date}&start=${start}&end=${end}&device_id=${currentDeviceId}&device_type=${currentDeviceType}`);
        let data = await res.json();
        
        setDates(data.dates, dateSelectId);
        points = data.points || [];
        
        if (currentDeviceType === 'mobile') {
            let s = document.getElementById('timeSlider');
            if (points.length > 0) {
                s.max = Math.max(0, points.length - 1);
                if (autoRefresh || fit) s.value = Math.max(0, points.length - 1);
                s.disabled = false;
                document.getElementById('mobileSummary').innerHTML = `<span class="badge">有效定位: ${data.returned_count} 点</span><span class="badge" style="background:#10b981;color:white">云端接收中</span>`;
                drawUntilSlider();
                if (fit && points.length > 1) map.setViewport(points.map(pt));
            } else {
                s.max = 0; s.value = 0; s.disabled = true;
                clearOverlays();
                updateMobileDashboard(null);
                document.getElementById('mobileSummary').innerHTML = `<span class="badge" style="background:#ea580c;color:white">无轨迹</span>`;
            }
        } else {
            // 定点站渲染逻辑
            if (points.length > 0) {
                let latest = points[points.length - 1];
                updateStationDashboard(latest);
                
                // 如果没有处于人为地图选点状态，就根据数据更新站址图标
                if (!isMapUnlocked && latest.lng && latest.lat) {
                    let mapPt = new BMap.Point(latest.lng, latest.lat);
                    drawStationMarker(mapPt);
                    if (fit) map.panTo(mapPt);
                }
            } else {
                updateStationDashboard(null);
            }
        }
    } catch(e) {}
}

// 新增一个走航车的全局缓存字典
let mobileDataCache = {};

function updateMobileDashboard(d) {
    if (!d) {
        ['pm25','pm10','voc','co2','temp','rh','speed','sat','fix'].forEach(k => document.getElementById(`val-${k}`).innerText = '--');
        return;
    }
    
    // 辅助更新函数（自带 Data Hold 数据保持记忆功能）
    const updateField = (key, domId, suffix = '') => {
        let raw = d[key];
        let newVal = val(raw); // 内部拦截函数，将 null/空字符串/NaN 都转为 '-'
        
        if (newVal === '-' && mobileDataCache[key] !== undefined && mobileDataCache[key] !== '-') {
            newVal = mobileDataCache[key]; // 如果当前无效且有历史数据，触发数据保持
        } else if (newVal !== '-') {
            mobileDataCache[key] = newVal; // 如果传来有效数据，更新缓存
        }
        
        let el = document.getElementById(domId);
        if(el) el.innerText = newVal !== '-' ? newVal + suffix : '--';
    };

    updateField('pm25', 'val-pm25');
    updateField('pm10', 'val-pm10');
    updateField('voc', 'val-voc');
    updateField('co2', 'val-co2');
    updateField('temp', 'val-temp', '℃');
    updateField('rh', 'val-rh', '%');
    updateField('speed', 'val-speed');
    updateField('satellites', 'val-sat');

    // 针对定位状态的特判处理
    let fixRaw = d.fix_quality;
    let fixText = '--';
    if (fixRaw !== null && fixRaw !== undefined) {
        if (fixRaw === 0) fixText = '搜索中';
        else if (fixRaw === 1) fixText = '单点解';
        else if (fixRaw === 2) fixText = '差分解';
        mobileDataCache['fix'] = fixText;
    } else if (mobileDataCache['fix']) {
        fixText = mobileDataCache['fix'];
    }
    let fixEl = document.getElementById('val-fix');
    if(fixEl) fixEl.innerText = fixText;
}

function updateStationDashboard(d) {
    return;
}

// 走航渲染管道
function currentSub() {
    if (points.length === 0) return [];
    let idx = Number(document.getElementById('timeSlider').value || points.length - 1);
    return points.slice(0, Math.max(0, Math.min(points.length - 1, idx)) + 1);
}

function drawTrack(sub) {
    clearOverlays();
    setActive('track');
    if (sub.length < 1) return;
    
    let l = new BMap.Polyline(sub.map(pt), {strokeColor: '#3b82f6', strokeWeight: 4, strokeOpacity: 0.9});
    map.addOverlay(l);
    overlays.push(l);
    
    let endMarker = new BMap.Marker(pt(sub[sub.length - 1]));
    map.addOverlay(endMarker);
    overlays.push(endMarker);
    endMarker.addEventListener('click', () => {
        map.openInfoWindow(new BMap.InfoWindow(popupMobile(sub[sub.length - 1])), pt(sub[sub.length - 1]));
    });
}

function drawValueLayer(sub, field) {
    clearOverlays();
    setActive(field);
    let vals = sub.map(d => d[field]).filter(v => v !== null && !isNaN(v));
    if (!vals.length) return drawTrack(sub);
    
    for (let d of sub) {
        if (d[field] === null || isNaN(d[field])) continue;
        let c = colorRamp(normalize(vals, d[field]));
        let circle = new BMap.Circle(pt(d), 3.5, {strokeColor: c, strokeWeight: 1, strokeOpacity: 0.8, fillColor: c, fillOpacity: 0.8});
        circle.addEventListener('click', () => map.openInfoWindow(new BMap.InfoWindow(popupMobile(d)), pt(d)));
        map.addOverlay(circle);
        overlays.push(circle);
    }
}

function drawMode(m) { currentLayer = m; drawTrack(currentSub()); }
function drawLayer(f) { currentLayer = f; drawValueLayer(currentSub(), f); }
function drawUntilSlider() {
    if (points.length === 0) return;
    if (currentLayer === 'track') drawTrack(currentSub());
    else drawValueLayer(currentSub(), currentLayer);
    updateMobileDashboard(currentSub()[currentSub().length - 1]);
}

function setDates(dates, selectId) {
    let sel = document.getElementById(selectId), old = sel.value;
    sel.innerHTML = '';
    if (!dates || dates.length === 0) dates = [new Date().toISOString().split('T')[0]];
    dates.forEach(x => {
        let o = document.createElement('option');
        o.value = x; o.text = x;
        sel.appendChild(o);
    });
    if (dates.includes(old)) sel.value = old;
    else if (dates.length) sel.value = dates[dates.length - 1];
}

async function init() {
    // 1. 首次加载时拉取设备列表
    await fetchDevices(false);
    
    // 2. 开启定时轮询
    setInterval(() => {
        if (autoRefresh && currentDeviceId) {
            // 拉取常规地图轨迹点与基础数据
            loadData(false);
            
            // 如果当前选中的是定点设备，额外拉取边缘端的最新对齐快照，以刷新 3x3 九宫格
            if (currentDeviceType === 'stationary') {
                fetchAndRenderEdgeSnapshot(currentDeviceId);
            }
        }
        
        // 刷新左侧设备列表的在线/离线状态
        fetchDevices(false); 
    }, REFRESH_INTERVAL);
}

function toggleAuto() {
    autoRefresh = !autoRefresh;
    document.getElementById('autoBtn').innerText = '自动刷新：' + (autoRefresh ? '开' : '关');
}

// 获取并刷新边缘端 3x3 综合面板

// 定义一个全局缓存字典，用于实现 Data Hold 逻辑
let edgeDataCache = {};

async function fetchAndRenderEdgeSnapshot(deviceId) {
    if (!deviceId) return;
    try {
        let res = await fetch(`/api/edge/snapshot/latest?device_id=${deviceId}`);
        let json = await res.json();
        
        if (json.ok && json.data) {
            let d = json.data;
            let isRunning = (d.status === '运行中');
            
            // 1. 动态渲染运行状态与颜色
            let statusBadge = document.getElementById('statDeviceStatus');
            statusBadge.innerText = d.status || '未知';
            if (isRunning) {
                statusBadge.style.background = '#4CAF50'; 
            } else if (d.status === '待命中') {
                statusBadge.style.background = '#f39c12'; 
            } else {
                statusBadge.style.background = '#666666'; 
            }

            // 2. 带有 Data Hold 逻辑的渲染过程
            // 映射关系：后端JSON的键名 -> 前端DOM的ID
            const fieldsMap = {
                'veh_count': 'stat-veh_count', 'ldv_count': 'stat-ldv_count', 'hdv_count': 'stat-hdv_count',
                'pm25': 'stat-pm25', 'pm10': 'stat-pm10', 'temp': 'stat-temp',
                'humidity': 'stat-rh', // 注意后端的 humidity 对应前端的 stat-rh
                'wind_speed': 'stat-wind_speed', 'wind_dir': 'stat-wind_dir'
            };

            for (let [key, domId] of Object.entries(fieldsMap)) {
                // val() 会把后端传来的 null, '', '--' 都转化为 '-'
                let newVal = val(d[key]);
                
                if (isRunning) {
                    // 【数据保持】：如果边缘端传来的是无效数据 '-'，但缓存里有以前的有效数据，就用旧数据续命
                    if (newVal === '-' && edgeDataCache[key] !== undefined && edgeDataCache[key] !== '-') {
                        newVal = edgeDataCache[key];
                    } else {
                        // 如果传来了真实的有效数据，立刻更新缓存
                        edgeDataCache[key] = newVal;
                    }
                } else {
                    // 非运行状态（待命中/未开启）：必须清空缓存，强制显示 '-'
                    edgeDataCache[key] = '-';
                    newVal = '-';
                }
                
                // 渲染到网页上
                let el = document.getElementById(domId);
                if (el) el.innerText = newVal;
            }
        }
    } catch (err) {
        console.error("拉取边缘设备快照失败:", err);
    }
}

init();