
# Y2H 云端监测大屏 - Systemd 后台服务维护手册

本文档记录了 Y2H 云端环境感知系统（基于 FastAPI）在 Linux 云服务器上的守护进程（Systemd）配置方案及日常运维指令。通过将 `server.py` 注册为系统服务，我们实现了网页后端的**开机自启**、**崩溃自动重启**以及**无头后台运行**。

## 1. 服务核心信息

* **服务名称**：`y2h-cloud.service`
* **运行端口**：`8000`
* **项目路径**：`/home/ubuntu/project/data-monitor/`
* **配置文件路径**：`/etc/systemd/system/y2h-cloud.service`

## 1.1 Y2H-RAG 智能研判模块

系统已内置 `/api/ai/query` 接口。默认情况下，即使没有大模型 API Key，也会使用近实时走航、定点和边缘快照数据进行本地风险计算，并结合内置治理知识库生成“哪里风险最高、为什么、怎么办”的证据约束回答。

如果需要接入外部大语言模型，请在服务启动环境中配置以下变量：

```bash
export Y2H_LLM_API_URL="https://你的模型服务/v1/chat/completions"
export Y2H_LLM_API_KEY="你的API_KEY"
export Y2H_LLM_MODEL="你的模型名称"
```

当前已用 `Qwen/Qwen2.5-7B-Instruct` 和 `Qwen/Qwen2.5-14B-Instruct` 测通过硅基流动 OpenAI 兼容接口。7B 更便宜，14B 的中文研判稳定性更好，演示建议优先使用 14B。正式部署到 systemd 时，可以在 `[Service]` 段加入占位配置：

```ini
Environment="Y2H_LLM_API_URL=https://api.siliconflow.cn/v1/chat/completions"
Environment="Y2H_LLM_API_KEY=替换为你的API_KEY"
Environment="Y2H_LLM_MODEL=Qwen/Qwen2.5-14B-Instruct"
Environment="Y2H_LLM_TIMEOUT=60"
Environment="Y2H_LLM_MAX_TOKENS=900"
```

不要把真实 API Key 提交到公开仓库。

未配置上述变量时，前端仍可正常使用本地 RAG 研判结果。

### 模拟数据测试

如果暂时没有真实设备上传数据，可在项目目录运行：

```bash
python tools/seed_ai_demo_data.py
```

脚本会写入 `DEMO_MOBILE_01` 和 `DEMO_STATION_01` 两个演示设备的近 2 小时数据，包括走航污染物、固定站数据和边缘视觉快照。然后在网页 AI 窗口提问：

```text
根据过去2小时的数据，哪里环境风险最高？应该怎么办？
```

AI 应能识别出一个高风险网格，并给出 PM、CO2、车辆活动、热风险等证据和治理建议。再次运行脚本会先替换旧的 `DEMO_*` 演示数据，不会删除真实设备数据。

需要清理演示数据时运行：

```bash
python tools/seed_ai_demo_data.py --clear-only
```

## 2. 配置文件备份

如果需要在新服务器上重新部署，请使用 `sudo nano /etc/systemd/system/y2h-cloud.service` 创建文件，并填入以下内容：

```ini
[Unit]
Description=Y2H Cloud Dashboard Service (FastAPI)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/project/data-monitor
ExecStart=/usr/bin/python3 /home/ubuntu/project/data-monitor/server.py

# 崩溃防线：意外退出后 5 秒自动重启
Restart=always
RestartSec=5

# 日志输出重定向
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=y2h-cloud

[Install]
WantedBy=multi-user.target

```

*(注：每次修改此配置文件后，必须执行 `sudo systemctl daemon-reload` 使其生效。)*

---

## 3. 日常运维命令速查表 (Cheat Sheet)

在云服务器终端中，使用以下指令管理大屏后端服务：

| 运维需求 | 执行命令 | 说明 |
| --- | --- | --- |
| **查看实时日志** | `journalctl -u y2h-cloud.service -f` | 实时滚动查看服务运行输出，排查报错。<br>

<br>按 `Ctrl + C` 退出。 |
| **查看运行状态** | `sudo systemctl status y2h-cloud.service` | 检查是否为绿色 `active (running)`。<br>

<br>按 `q` 键退出状态界面。 |
| **代码更新后重启** | `sudo systemctl restart y2h-cloud.service` | 每次通过 FTP/Git 上传了新的 Python 代码后，**必须执行此命令**让新代码生效。 |
| **临时停止服务** | `sudo systemctl stop y2h-cloud.service` | 关闭后端，释放 8000 端口和数据库锁。 |
| **手动启动服务** | `sudo systemctl start y2h-cloud.service` | 启动处于 inactive 状态的服务。 |
| **开启开机自启** | `sudo systemctl enable y2h-cloud.service` | 允许服务在云服务器重启后自动拉起。 |
| **关闭开机自启** | `sudo systemctl disable y2h-cloud.service` | 永久关闭自动启动。 |

---

## 4. 常见问题排查 (FAQ)

### ❓ 报错：`Address already in use` 或端口被占用卡死

**原因**：通常是因为之前手动在终端运行了 `server.py` 但没有正常关闭，或者是服务发生了死锁现象。
**解决方案**：

1. 强制击杀占用 8000 端口的僵尸进程：
```bash
sudo fuser -k 8000/tcp

```



```
2. 重新启动服务：
   ```bash
   sudo systemctl restart y2h-cloud.service

```

### ❓ 本地浏览器可以打开网址，但手机或外部网络打不开

**原因**：云服务器（如阿里云、腾讯云等）的安全组/防火墙未放行 `8000` 端口。
**解决方案**：
登录云服务器厂商的网页控制台，找到**安全组 (Security Group)** 或**防火墙**设置，添加一条入方向规则，允许 **TCP 8000 端口** 即可。

### ❓ 访问网页时提示 `{"detail":"Not Found"}`

**原因**：URL 路径输入有误，FastAPI 未能匹配到对应路由。
**解决方案**：
确保直接访问 `http://你的IP:8000`，结尾不要加 `/index.html`，也不要带有任何 URL 转义字符。如果电脑端异常，请检查是否开启了代理软件（如 Clash/V2Ray 等），尝试将其设为直连模式。
