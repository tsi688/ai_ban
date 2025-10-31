
安装python库

pip install binance-futures-connector

pip install websocket-client

pip install pandas

pip install openai

#需要修改部分


# 币安api
api_key = ''

api_secret = ''

deepseek api

ds_api = ''

#报错重试每秒重试一次，最多重试20次

max_retries=20

retry_delay=1

#合约交易对大写

symbol = 'DOGEUSDT'

#开仓数量usdt，跟杠杠倍数无关，总共下单8u

start_usdt = 8

#杠杠倍数

leverage = 20

#K线部分
#k线周期  只能从这里选，1m 3m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 3d 1w 1M

interval = '5m' 

#获取多少天历史K线数据

day =1 

#df保留多少根k线喂给ds
max_rows = 100   

运行

前台运行（客户端关闭会退出）

python ai_ban.py

后台运行(推荐)

nohup python -u ai_ban.py > ai_ban.log 2>&1 &

查看PID

ps -ef|grep py

关闭后台程序

kill PID

查看日志

tail -f ai_ban.log
