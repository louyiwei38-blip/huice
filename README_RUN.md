说明见 [README.md](README.md)。

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python detect.py --align    # IS：N=4184、胜率 59.44%（不跳过北京 20–23）
python live.py --loop       # 实盘：周一至周五跳过北京 20–23；下单走 webhook
```
