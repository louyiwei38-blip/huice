# RSI_BB 实盘（huice `rsibb` 分支）

15m RSI(7) 20/80 + 布林 k=2.2。5m 收盘出信号后推 Telegram，并在 [跟单面板](http://194.233.90.109:3000) 自动下单（30 分钟、50 USDT）。

回放与规则说明见 `README_RUN.md`。

## 服务器 PM2

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm i -g pm2

git clone -b rsibb https://github.com/louyiwei38-blip/huice.git
cd huice
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
cp .env.example .env
nano .env   # 填 COPYBOT_* 与 TELEGRAM_*

python live.py --test-copybot
mkdir -p logs
pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

```bash
pm2 logs rsibb
pm2 restart rsibb
git pull origin rsibb && pm2 restart rsibb
```

不要把 `.env` 提交到 git。
