
# Y2H 云端监测大屏 - Systemd 后台服务维护手册

本文档记录了 Y2H 云端环境感知系统（基于 FastAPI）在 Linux 云服务器上的守护进程（Systemd）配置方案及日常运维指令。通过将 `server.py` 注册为系统服务，我们实现了网页后端的**开机自启**、**崩溃自动重启**以及**无头后台运行**。

## 1. 服务核心信息

* **服务名称**：`y2h-cloud.service`
* **运行端口**：`8000`
* **项目路径**：`/home/ubuntu/project/data-monitor/`
* **配置文件路径**：`/etc/systemd/system/y2h-cloud.service`

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
