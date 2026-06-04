let map = new BMap.Map('map', {enableMapClick: false});
map.centerAndZoom(new BMap.Point(118.79, 32.06), 15);
map.enableDragging();
map.enableInertialDragging();
map.enableScrollWheelZoom(true);

let overlays = [], points = [];
let currentLayer = 'track', autoRefresh = true;
let aiOverlays = [];

// 状态机变量
let currentDeviceId = null; 
let currentDeviceType = null; 
let isPanelCollapsed = false; // 控制面板是否折叠的状态标志
let isAiPanelOpen = true;
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
function escapeHtml(text) {
    return String(text ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
}

function clearAiOverlays() {
    aiOverlays.forEach(o => map.removeOverlay(o));
    aiOverlays = [];
}

function toggleAiAdvisor(forceOpen) {
    if (typeof forceOpen === 'boolean') {
        isAiPanelOpen = forceOpen;
    } else {
        isAiPanelOpen = !isAiPanelOpen;
    }

    const panel = document.getElementById('aiPanel');
    const fab = document.getElementById('aiFab');
    if (!panel || !fab) return;

    panel.classList.toggle('open', isAiPanelOpen);
    fab.classList.toggle('active', isAiPanelOpen);
    fab.setAttribute('aria-expanded', String(isAiPanelOpen));
}

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

// ======================== Y2H-RAG 智能研判助手 ========================
async function askAiAdvisor() {
    const questionEl = document.getElementById('aiQuestion');
    const hoursEl = document.getElementById('aiHours');
    const statusEl = document.getElementById('aiStatus');
    const resultEl = document.getElementById('aiResult');
    const panelEl = document.getElementById('aiPanel');
    const question = questionEl.value.trim();
    const hours = Number(hoursEl.value || 2);

    if (!question) {
        statusEl.innerText = '请输入需要研判的问题。';
        return;
    }

    statusEl.innerText = '正在聚合近实时数据并检索治理知识库...';
    if (panelEl) panelEl.classList.add('has-result');
    resultEl.style.display = 'block';
    resultEl.innerHTML = '<div class="ai-loading">研判中...</div>';

    try {
        const res = await fetch('/api/ai/query', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({question, hours, use_llm: true})
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
            throw new Error(data.detail || 'AI研判失败');
        }
        statusEl.innerText = data.mode === 'llm-rag'
            ? '已使用大模型 + RAG 生成回答。'
            : '已使用本地风险计算 + RAG 知识库生成回答。';
        renderAiResult(data);
    } catch (err) {
        console.error('AI advisor error', err);
        statusEl.innerText = 'AI研判失败，请检查后端服务。';
        resultEl.innerHTML = `<div class="ai-error">${escapeHtml(err.message || err)}</div>`;
    }
}

function renderAiResult(data) {
    const resultEl = document.getElementById('aiResult');
    const cards = data.risk_cards || [];
    const knowledge = data.knowledge || [];

    let cardsHtml = cards.length ? cards.map((card, idx) => {
        const lng = Number(card.bd_lng || 0);
        const lat = Number(card.bd_lat || 0);
        const canFocus = lng && lat;
        const evidence = (card.evidence || []).slice(0, 2).map(escapeHtml).join('；') || '暂无关键证据';
        return `
            <button class="ai-risk-card" ${canFocus ? `onclick="focusAiRisk(${lng}, ${lat}, ${Number(card.risk_score || 0)})"` : ''}>
                <span class="ai-rank">#${idx + 1}</span>
                <span class="ai-risk-main">
                    <b>${escapeHtml(card.risk_level)} · ${escapeHtml(card.risk_score)}/100</b>
                    <small>${escapeHtml((card.sources || []).join(' / '))} · ${escapeHtml(card.sample_count)} samples</small>
                    <em>${evidence}</em>
                </span>
            </button>
        `;
    }).join('') : '<div class="ai-empty">暂无可定位的风险网格。</div>';

    let knowledgeHtml = knowledge.length ? knowledge.map(item => `
        <div class="ai-knowledge-item">
            <b>${escapeHtml(item.title)}</b>
            <span>${escapeHtml(item.content)}</span>
        </div>
    `).join('') : '';

    resultEl.innerHTML = `
        <div class="ai-answer">${escapeHtml(data.answer)}</div>
        <div class="ai-subtitle">风险网格</div>
        <div class="ai-risk-list">${cardsHtml}</div>
        <div class="ai-subtitle">检索依据</div>
        <div class="ai-knowledge">${knowledgeHtml}</div>
        <div class="ai-footnote">数据窗口：近 ${escapeHtml(data.hours)} 小时；走航 ${escapeHtml(data.data_counters?.mobile_rows ?? 0)} 条，定点 ${escapeHtml(data.data_counters?.stationary_rows ?? 0)} 条，边缘快照 ${escapeHtml(data.data_counters?.edge_rows ?? 0)} 条。</div>
    `;
}

function focusAiRisk(lng, lat, score) {
    if (!lng || !lat) return;
    clearAiOverlays();
    const p = new BMap.Point(lng, lat);
    map.panTo(p);
    if (map.getZoom() < 17) map.setZoom(17);

    const radius = score >= 75 ? 90 : score >= 50 ? 70 : 50;
    const circle = new BMap.Circle(p, radius, {
        strokeColor: '#f97316',
        strokeWeight: 2,
        strokeOpacity: 0.9,
        fillColor: '#f97316',
        fillOpacity: 0.22
    });
    const label = new BMap.Label(`AI风险 ${score}/100`, {position: p, offset: new BMap.Size(12, -28)});
    label.setStyle({
        color: '#111827',
        backgroundColor: '#fbbf24',
        border: '0',
        borderRadius: '6px',
        padding: '5px 8px',
        fontWeight: '700'
    });
    map.addOverlay(circle);
    map.addOverlay(label);
    aiOverlays.push(circle, label);
}

// ======================== 数据加载与渲染核心 ========================
async function fetchDevices(autoSelect = false) {
    try {
        let res = await fetch('/api/devices');
        let data = await res.json();
        
        let mobileHtml = '';
        let statHtml = '';
        
        data.devices.forEach(d => {
            // 【核心修改 1】：引入状态机映射，新增定位中 (locating) 的黄色样式
            let statusObj = {
                'online': { text: '在线', dotBg: '#10b981', badgeBg: '#10b981' }, // 绿灯
                'locating': { text: '定位中', dotBg: '#f59e0b', badgeBg: '#f59e0b' }, // 黄灯
                'offline': { text: '离线', dotBg: '#64748b', badgeBg: '#334155' } // 灰灯
            }[d.status] || { text: '未知', dotBg: '#64748b', badgeBg: '#334155' };

            let activeClass = (d.id === currentDeviceId) ? 'active-device' : '';
            
            // 注意这里去掉了 statusClass，直接使用 style 内联渲染颜色，防止没有对应 css 类
            let item = `
            <div class="device-item ${activeClass}" data-id="${d.id}" onclick="selectDevice('${d.id}', '${d.type}')">
                <div>
                    <div class="d-name"><span class="status-dot" style="background-color: ${statusObj.dotBg}"></span>${d.id}</div>
                    <div class="d-time">最后通讯: ${d.last_seen}</div>
                </div>
                <div style="text-align:right;">
                    <div class="badge" style="background:${statusObj.badgeBg}; color:white; border:0; margin-bottom:4px">${statusObj.text}</div><br>
                    <span style="font-size:11px; color:#94a3b8">${d.total_points} pts</span>
                </div>
            </div>`;
            
            if (d.type === 'mobile') mobileHtml += item;
            else statHtml += item;
        });
        
        document.getElementById('mobileDeviceList').innerHTML = mobileHtml || '<div class="small">暂无设备</div>';
        document.getElementById('stationaryDeviceList').innerHTML = statHtml || '<div class="small">暂无固定站设备</div>';
        
    } catch(e) {
        console.error("加载设备列表失败", e);
    }
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
                
                // 【核心新增】：通过最新一条数据的 gps_state 决定上方徽章的内容
                let latestPoint = points[points.length - 1]; // 取出当前最新数据点
                let isLocating = latestPoint.gps_state === 'locating';
                
                let statusBadgeHtml = isLocating ? 
                    `<span class="badge" style="background:#f59e0b;color:white">🟡 定位中</span>` : 
                    `<span class="badge" style="background:#10b981;color:white">🟢 已定位</span>`;
                
                document.getElementById('mobileSummary').innerHTML = `<span class="badge">已接收: ${data.returned_count} 点</span>${statusBadgeHtml}`;
                
                drawUntilSlider();
                
                let validPointsForView = points.filter(p => p.lng !== null && p.lat !== null);
                if (fit && validPointsForView.length > 1) {
                    map.setViewport(validPointsForView.map(pt));
                }
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
        ['pm25','pm10','voc','co2','temp','rh','speed','sat','snr'].forEach(k => {
            let el = document.getElementById(`val-${k}`);
            if (el) el.innerText = '--';
        });
        return;
    }
    
    const updateField = (key, domId, suffix = '') => {
        let raw = d[key];
        let newVal = val(raw); 
        
        if (newVal === '-' && mobileDataCache[key] !== undefined && mobileDataCache[key] !== '-') {
            newVal = mobileDataCache[key]; 
        } else if (newVal !== '-') {
            mobileDataCache[key] = newVal; 
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
    updateField('speed', 'val-speed', ' km/h');
    updateField('satellites', 'val-sat');
    updateField('snr', 'val-snr', ' dB');
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
    
    let validData = sub.filter(p => p.lng !== null && p.lat !== null);
    if (validData.length < 1) return;
    
    let l = new BMap.Polyline(validData.map(pt), {strokeColor: '#3b82f6', strokeWeight: 4, strokeOpacity: 0.9});
    map.addOverlay(l);
    overlays.push(l);
    
    let lastValidPt = validData[validData.length - 1];
    let endPt = pt(lastValidPt);
    let endMarker = new BMap.Marker(endPt);
    map.addOverlay(endMarker);
    overlays.push(endMarker);
    
    // 基于 hdop 绘制定位精度圈
    // 如果 hdop > 1.2 认为存在一定误差，绘制半透明圆 (1.2以内认为是完美精度，不画圈)
    // 经验公式估算误差半径：HDOP * 5 (米)
    if (lastValidPt.hdop && lastValidPt.hdop > 1.2) {
        let radius = lastValidPt.hdop * 5; 
        let circle = new BMap.Circle(endPt, radius, {
            strokeColor: "#3b82f6", 
            strokeWeight: 1, 
            strokeOpacity: 0.3, 
            fillColor: "#3b82f6", 
            fillOpacity: 0.15
        });
        map.addOverlay(circle);
        overlays.push(circle);
    }
    
    endMarker.addEventListener('click', () => {
        map.openInfoWindow(new BMap.InfoWindow(popupMobile(lastValidPt)), endPt);
    });
}

function drawValueLayer(sub, field) {
    clearOverlays();
    setActive(field);
    
    // 【核心修改 3】：同样过滤掉没有 GPS 坐标的点
    let validData = sub.filter(p => p.lng !== null && p.lat !== null);
    if (validData.length < 1) return;

    let vals = validData.map(d => d[field]).filter(v => v !== null && !isNaN(v));
    if (!vals.length) return drawTrack(validData);
    
    for (let d of validData) {
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
                'ground_temp': 'stat-ground_temp', 'ldv_count': 'stat-ldv_count', 'hdv_count': 'stat-hdv_count',
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
