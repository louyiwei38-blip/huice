# Range MR V1.1 信号机器人

BTCUSDT + ETHUSDT 30m 盘整回归 / 突破互换信号检测。只推送 Telegram，不下单。结算按赢 +0.85 / 输 -1。

## 服务器部署（Ubuntu）

1. 安装依赖

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

2. 拉代码（xinhao 分支）

```bash
sudo mkdir -p /opt/huice
sudo chown "$USER:$USER" /opt/huice
git clone -b xinhao https://github.com/louyiwei38-blip/huice.git /opt/huice
cd /opt/huice
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. 配置 Telegram（不要把 token 提交到 git）

```bash
cp data/telegram.json.example data/telegram.json
nano data/telegram.json
```

填入：

```json
{
  "bot_token": "你的Bot Token",
  "chat_id": "你的Chat ID"
}
```

4. 先试推一条（确认 TG 能收到）

```bash
cd /opt/huice
source .venv/bin/activate
python -c "from range_mr.config import Config; from range_mr.telegram_notify import load_telegram, send_telegram; t,c=load_telegram(Config()); send_telegram(t,c,'服务器部署成功')"
```

5. 用 systemd 常驻

```bash
sudo cp deploy/range-mr.service /etc/systemd/system/range-mr.service
sudo nano /etc/systemd/system/range-mr.service
```

把 `WorkingDirectory` 和 `ExecStart` 改成实际路径，例如：

```
WorkingDirectory=/opt/huice
ExecStart=/opt/huice/.venv/bin/python -u /opt/huice/run_server.py
```

然后：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now range-mr
sudo systemctl status range-mr
```

6. 看日志

```bash
journalctl -u range-mr -f
# 或
tail -f /opt/huice/logs/live.log
```

7. 更新代码

```bash
cd /opt/huice
git pull origin xinhao
sudo systemctl restart range-mr
```

停掉：`sudo systemctl stop range-mr`
