import logging,time,threading,json,websocket
from binance.um_futures import UMFutures
import pandas as pd
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
import queue,os
from openai import OpenAI

# ================= 异步日志系统 =================
# === 动态日志文件名 ===
log_filename = os.path.splitext(os.path.basename(__file__))[0] + ".log"

log_queue = queue.Queue(-1)
file_handler = RotatingFileHandler(
    log_filename, maxBytes=10*1024*1024, backupCount=3, encoding="utf-8"
)
queue_handler = QueueHandler(log_queue)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[queue_handler]
)

listener = QueueListener(log_queue, file_handler)
listener.start()

# 控制台也输出日志
console = logging.StreamHandler()
logging.getLogger().addHandler(console)


# 设置显示最大列宽、行数、列数
pd.set_option('display.max_rows', None)   # 显示所有行
pd.set_option('display.max_columns', None)  # 显示所有列
pd.set_option('display.width', None)     # 自动调整宽度，不换行
pd.set_option('display.max_colwidth', None)  # 每列完整显示，不截断



# 币安api
api_key = ''
api_secret = ''

#deepseek api
ds_api = ''

#报错重试每秒重试一次，最多重试20次
max_retries=20
retry_delay=1

#合约交易对
symbol = 'DOGEUSDT'

#开仓数量usdt
start_usdt = 8

#杠杠倍数
leverage = 20

#K线部分
interval = '5m'                                    #k线周期  1m 3m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 3d 1w 1M
day =1                                              #获取多少天历史K线数据
max_rows = 100                                      #df保留多少根k线喂给ds



#以下东西不要修改
#币安合约ws网址
socket_base_url = "wss://fstream.binance.com/stream?streams="

end_time = int(time.time() * 1000)
start_time = end_time - day * 24 * 60 * 60 * 1000   # 获取过去一年的数据365 * 24 * 60 * 60 * 1000

# 创建DataFrame来保存历史和实时K线数据
kline_df = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

# 监听的数据类型列表
market_data = [
    'aggTrade', #最新价格
    # 'miniTicker',
    # 'bookTicker',
    # f"depth{'5@100ms'}",
    f'kline_{interval}',    #k线
    ]


#全局变量
#最新价格
new_price = 0.0
#持仓数量
positionAmt = 0.0
#保存ai输出的信号
signal_data = None
#止盈价格
stop_loss = 0.0
#止损价格
take_profit = 0.0

#标记获取到最新价格
is_get_new_price = threading.Event()
# #标记K线获取完成
is_true_kline = threading.Event()
#标记ai输出信号完成
is_ai_finish = threading.Event()

#获取交易对的价格小数点最小精度和下单数量的小数点最小精度
def get_symbol_info(symbol):
    for attempt in range(max_retries):
        try:   
            data = client.exchange_info()
            for s in data['symbols']:
                if s['symbol'] == symbol:
                    for filter in s['filters']:
                        if filter['filterType'] == 'PRICE_FILTER':
                            tick_size = filter['tickSize']
                        if filter['filterType'] == 'LOT_SIZE':
                            step_size = filter['stepSize']
                            minQty = float(filter['minQty'])
                        if filter['filterType'] == 'MIN_NOTIONAL':
                            notional = float(filter['notional'])
                    # 计算小数点位数
                    price_precision = len(tick_size.rstrip('0').split('.')[1]) if '.' in tick_size else 0
                    quantity_precision = len(step_size.rstrip('0').split('.')[1]) if '.' in step_size else 0
                    
                    return quantity_precision
        
        except Exception as e:
            # 处理其他异常情况
            logging.error(f"其他错误: {e}")
            if attempt + 1 == max_retries:
                logging.error(f"{symbol}获取交易对的价格小数点最小精度和下单数量的小数点最小精度最终失败")
                return 1
            # 非超负荷错误，进行常规重试
            time.sleep(retry_delay)

#获取最大杠杠倍数
def get_leverage(symbol):
    for attempt in range(max_retries):
        try:
            response = client.leverage_brackets(symbol=symbol, recvWindow=6000)
            leverage = int(response[0]['brackets'][0]['initialLeverage'])
            return leverage
        
        except Exception as e:
            # 处理其他异常情况
            logging.error(f"其他错误: {e}")
            if attempt + 1 == max_retries:
                logging.error(f"{symbol}获取最大杠杠倍数最终失败")
                return None
            time.sleep(retry_delay)

#设置杠杆倍数
def change_leverage(symbol,leverage):
    for attempt in range(max_retries):
        try:
            response = client.change_leverage(
                symbol=symbol, leverage=leverage, recvWindow=6000
            )
            logging.info(f'{symbol}杠杆倍数设置为:{leverage}倍')
            return response
        
        except Exception as e:
            # 处理其他异常情况
            logging.error(f"其他错误: {e}")
            if attempt + 1 == max_retries:
                logging.error(f"{symbol}设置杠杆倍数最终失败")
                return None
            time.sleep(retry_delay)

#下market单(市价单)
def market_orders(symbol,quantity,side):
    for attempt in range(max_retries):
        try:
            response = client.new_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=round(quantity, quantity_precision),
                )             
            order_id = response['orderId']
            return order_id 
        
        except Exception as e:
            # 处理其他异常情况
            logging.error(f"发生错误: {e}")
            if attempt + 1 == max_retries:
                logging.error(f"{symbol}下市价单最终失败")
                return None
            time.sleep(retry_delay)

# 获取历史K线数据
def get_klines(symbol, interval, start_time, end_time):
    client = UMFutures()
    for attempt in range(max_retries):
        try:
            response = client.klines(
                symbol=symbol,
                interval=interval,
                startTime=start_time,
                endTime=end_time,
                limit=1500
            )
            return response
        
        except Exception as e:
            # 处理其他异常情况
            logging.error(f"发生错误: {e}")
            if attempt + 1 == max_retries:
                logging.error(f"获取{symbol}K线最终失败")
                return None
            time.sleep(retry_delay)

# 获取从现在开始到一年前的历史K线数据
def get_historical_klines(symbol, interval,start_time,end_time):

    all_klines = []
    while start_time < end_time:
        klines = get_klines(symbol, interval, start_time, end_time)
        if not klines:
            break
        all_klines.extend(klines)
        start_time = klines[-1][0] + 1  # 更新开始时间为最后一个K线的结束时间

        # 避免请求过于频繁
        time.sleep(0.01)

    return all_klines

# 将K线数据转换为DataFrame
def klines_to_dataframe(klines):
    global kline_df
    df = pd.DataFrame(klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time',
        'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume',
        'taker_buy_quote_asset_volume', 'ignore'
    ])   
    df['timestamp'] = pd.to_datetime(df['close_time'], unit='ms')  # 转换为时间格式
    df.set_index('timestamp', inplace=True)   
    kline_df = df[['open', 'high', 'low', 'close', 'volume']].astype(float)

# WebSocket公共行情数据消息处理
def market_message(ws, message):
    global kline_df,new_price,symbol

    try:
        message_data = json.loads(message)  # 解析消息
    except Exception as e:
        logging.error(f"user_data 解析消息失败: {e}")  # 解析失败
        return

    if "stream" in message_data and "data" in message_data:  # 组合流格式
        market_data = message_data.get('data',None)

        if market_data.get('e',None) == 'aggTrade':
            if symbol == market_data.get('s'):
                new_price = float(market_data.get('p'))
                is_get_new_price.set()
        
        elif 'kline' == market_data.get('e',None):
            kline_data = market_data["k"]
            k_true = kline_data["x"] #k线是否闭合true，false

            if symbol == market_data["s"]:           
                if k_true:
                    kline = {
                    'timestamp': pd.to_datetime(float(kline_data['T']), unit='ms'),  # 转换为时间格式 
                    'open': float(kline_data['o']),
                    'high': float(kline_data['h']),
                    'low': float(kline_data['l']),
                    'close': float(kline_data['c']),
                    'volume': float(kline_data['v']),
                    }

                    # 如果新的K线时间戳已经存在，则更新最后一行，否则追加新K线
                    kline_df.loc[kline['timestamp'], ['open', 'high', 'low', 'close', 'volume']] = [kline['open'], kline['high'], kline['low'], kline['close'], kline['volume']]
                    
                    is_true_kline.set()
                    logging.info(f'{kline_df.tail(10)}')
      
#ws连接，获取公共行情数据（k线数据，最新价格等）
class MarketDataWebSocket(threading.Thread):
    def __init__(self, streams, name=None):
        super().__init__(daemon=True)
        self.streams = streams
        self.url = socket_base_url + "/".join(streams)
        #断开多少秒重连
        self.reconnect_delay = 1

        self._stop_event = threading.Event()
        self.ws = None

        if name:
            self.name = name
        else:
            self.name = f"WSThread-{id(self)}"

    def run(self):
        while not self._stop_event.is_set():
            try:
                self.ws = websocket.WebSocketApp(
                    url=self.url,
                    on_open=self.on_open,
                    on_message=market_message,
                    on_error=self.on_error,
                    on_close=self.on_close
                )
                logging.info(f"线程 {self.name} 开始连接")
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                logging.error(f"线程 {self.name} 异常: {e}")
                self.ws = None

            if self.ws:
                try:
                    self.ws.close()
                    self.ws = None
                    logging.info(f"线程 {self.name} WebSocket 显式关闭以准备重建连接")
                except Exception as e:
                    logging.warning(f"线程 {self.name} 显式关闭失败: {e}")

            if not self._stop_event.is_set():
                logging.info(f"{self.reconnect_delay}s 后尝试重连...")
                time.sleep(self.reconnect_delay)

    def on_open(self, ws):
        logging.info(f"线程 {self.name} 已连接: {self.streams}")

    def on_error(self, ws, error):
        logging.warning(f"线程 {self.name} 出错: {error}")
        # 不手动 close，交由 run_forever 管理

    def on_close(self, ws, *args):
        logging.warning(f"线程 {self.name} 连接关闭: {self.streams}")

    def stop(self):
        self._stop_event.set()
        if self.ws:
            try:
                self.ws.close()
            except Exception as e:
                logging.warning(f"线程 {self.name} 显式关闭 ws 失败: {e}")

# 处理收到的账户消息（所有 symbol 的订单变动都会走这里）
def user_data_message(ws, message):
    global symbol,positionAmt
    try:
        message_data = json.loads(message)  # 解析消息
    except Exception as e:
        logging.error(f"user_data 解析消息失败: {e}")  # 解析失败
        return
    
    #存储当前仓位
    if message_data.get('e', None) == "ACCOUNT_UPDATE":  # 账户更新事件
        # 提取账户更新信息
        account_update = message_data["a"]
        # 获取当前仓位信息
        positions = account_update.get("P", [])  # "P"字段包含持仓信息
        # logging.info(positions)
        for position in positions:  
            if symbol == position["s"]:
                positionAmt = float(position["pa"])        #持仓数量
                # entry_price = float(position["ep"])         #入仓价格

#ws连接，获取用户数据数据（成交数据，下单数据等）
class UserDataWebSocket(threading.Thread):
    def __init__(self, name=None):
        super().__init__(daemon=True)
        self.reconnect_delay = 1

        self.listenKey = client.new_listen_key()["listenKey"]  # 🔧 listenKey 变为实例变量
        self.url = 'wss://fstream.binance.com/ws/' + self.listenKey

        self._stop_event = threading.Event()
        self.ws = None

        self.name = name or f"WSThread-{id(self)}"

    def run(self):
        while not self._stop_event.is_set():
            try:
                self.ws = websocket.WebSocketApp(
                    url=self.url,
                    on_open=self.on_open,
                    on_message=user_data_message,
                    on_error=self.on_error,
                    on_close=self.on_close
                )
                logging.info(f"线程 {self.name} 开始连接")
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                logging.error(f"线程 {self.name} 异常: {e}")
                self.ws = None

            if self.ws:
                try:
                    self.ws.close()
                    self.ws = None
                    logging.info(f"线程 {self.name} WebSocket 显式关闭以准备重建连接")
                except Exception as e:
                    logging.warning(f"线程 {self.name} 显式关闭失败: {e}")

            if not self._stop_event.is_set():
                logging.info(f"{self.reconnect_delay}s 后尝试重连...")
                time.sleep(self.reconnect_delay)

    def on_open(self, ws):
        logging.info(f"线程 {self.name} 已连接")

    def on_error(self, ws, error):
        logging.warning(f"线程 {self.name} 出错: {error}")

    def on_close(self, ws, *args):
        logging.warning(f"线程 {self.name} 连接关闭")

    def stop(self):
        self._stop_event.set()
        if self.ws:
            try:
                self.ws.close()
            except Exception as e:
                logging.warning(f"线程 {self.name} 显式关闭 ws 失败: {e}")

#ai处理,输出信号
def ds():
    global kline_df,signal_data,positionAmt

    while True:
        is_true_kline.wait()

        #保留多少行
        if len(kline_df) > max_rows:
            kline_df = kline_df.iloc[-max_rows:]
        
        prompt = f"""
                你是一个专业的加密货币交易分析师。需要在给定的{symbol}k线数据基础上结合趋势、支撑/阻力位、成交量以及技术指标来给出具体的交易信号：

                {kline_df}
                {positionAmt}


                【分析要求】
                1.K线趋势：通过裸K（即K线的开盘、收盘、最高和最低价）分析当前的价格走势。通过观察价格的高低点、K线形态（如吞没、锤头等）来判断趋势。
                2.支撑/阻力：根据历史的价格区间来找到潜在的支撑位和阻力位，这些是价格可能反转的关键区域。
                3.成交量：成交量放大通常意味着市场参与者的活跃度增加，且有可能支持价格突破或反转。
                4.根据这些分析，结合当前持仓，没有持仓positionAmt=0，持有多仓positionAmt>0,持有空仓positionAmt<0,给出交易信号：BUY (买入) / SELL (卖出) / HOLD (观望)/ CLOSE (平仓)。
                5. 基于技术分析建议合理的止损价位stop_loss
                6. 基于技术分析建议合理的止盈价位take_profit
                7. 评估信号信心程度

                请用以下JSON格式回复：
                {{
                    "signal": "BUY|SELL|HOLD|CLOSE",
                    "stop_loss": 具体价格,
                    "take_profit": 具体价格,
                    "confidence": "high|medium|low",
                    "reason": "分析理由",
                }}
                """

        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system",
                "content": f"您是一位急需资金为母亲治疗癌症的交易员，你现在需要从加密货币交易市场赚取足够的金额治疗你的母亲，"},
                {"role": "user", "content": prompt}
            ],
            stream=False
        )
        #解析json
        result = response.choices[0].message.content
        start_idx = result.find('{')
        end_idx = result.rfind('}') + 1
        if start_idx != -1 and end_idx != 0:
            json_str = result[start_idx:end_idx]
            signal_data = json.loads(json_str)
        else:
            logging.info(f"无法解析JSON: {result}")
            return None
        logging.info(signal_data)
        is_ai_finish.set()
        is_true_kline.clear()

#根据信号是否执行开仓
def open_trade():
    global signal_data,stop_loss,take_profit
    while True:
        is_ai_finish.wait()

        #信号BUY,SELL,HOLD,CLOSE
        signal = signal_data['signal']      
        #开仓信心high,low,medium
        confidence = signal_data['confidence']
        
        #当前没有持仓
        if positionAmt == 0:
            #开仓信心高
            if confidence != 'low':
                if signal == 'BUY' or signal == 'SELL':
                    order_id = market_orders(symbol=symbol,quantity=(start_usdt/new_price),side=signal)
                    if order_id != None:
                        logging.info(f'{symbol}下单{signal}成功')
                        #更新止盈止损
                        #止损价格
                        stop_loss = float(signal_data['stop_loss'])
                        #止盈价格
                        take_profit = float(signal_data['take_profit'])
        
        #持有多仓
        elif positionAmt > 0:
            #不操作
            if signal == 'BUY' or signal == 'HOLD' :
                #更新止盈止损
                #止损价格
                stop_loss = float(signal_data['stop_loss'])
                #止盈价格
                take_profit = float(signal_data['take_profit'])
            
            elif signal == 'SELL':
                #反向做空
                order_id = market_orders(symbol=symbol,quantity=positionAmt*2,side=signal)
                if order_id != None:
                    logging.info(f'{symbol}下单{signal}成功')
                    #更新止盈止损
                    #止损价格
                    stop_loss = float(signal_data['stop_loss'])
                    #止盈价格
                    take_profit = float(signal_data['take_profit'])
            
            elif signal == 'CLOSE' :
                #平仓
                order_id = market_orders(symbol=symbol,quantity=positionAmt,side='SELL')
                if order_id != None:
                    logging.info(f'{symbol}平仓成功')
        
        #持有空仓
        elif positionAmt < 0:
            #不操作
            if signal == 'SELL' or signal == 'HOLD':
                #更新止盈止损
                #止损价格
                stop_loss = float(signal_data['stop_loss'])
                #止盈价格
                take_profit = float(signal_data['take_profit'])

            elif signal == 'BUY':
                #反向做多
                order_id = market_orders(symbol=symbol,quantity=abs(positionAmt*2),side=signal)
                if order_id != None:
                    logging.info(f'{symbol}下单{signal}成功')
                    #更新止盈止损
                    #止损价格
                    stop_loss = float(signal_data['stop_loss'])
                    #止盈价格
                    take_profit = float(signal_data['take_profit'])
            
            elif signal == 'CLOSE' :
                #平仓
                order_id = market_orders(symbol=symbol,quantity=abs(positionAmt),side='BUY')
                if order_id != None:
                    logging.info(f'{symbol}平仓成功')

        is_ai_finish.clear()

#止盈止损
def close_trade():
    global stop_loss,take_profit,positionAmt
    while True:
        is_get_new_price.wait()

        if positionAmt == 0:
            is_get_new_price.clear()
            continue

        elif positionAmt > 0:  
            logging.info(f'最新价格{new_price},开仓价格{entry_price},止盈价格{take_profit},止损价格{stop_loss},总盈利{total_profit:.4f}usdt,开仓方向：BUY')
            is_get_new_price.clear()      
            #止盈
            if new_price > take_profit: 
                order_id = market_orders(symbol=symbol,quantity=positionAmt,side='SELL')

                if order_id != None:
                    logging.info(f'{symbol}止盈成功')
            #止损
            elif new_price <=stop_loss :
                order_id = market_orders(symbol=symbol,quantity=positionAmt,side='SELL')

                if order_id != None:
                    logging.info(f'{symbol}止损成功')
        
        elif positionAmt < 0:
            logging.info(f'最新价格{new_price},开仓价格{entry_price},止盈价格{take_profit},止损价格{stop_loss},总盈利{total_profit:.4f}usdt,开仓方向：SELL') 
            is_get_new_price.clear()
            #止盈
            if new_price < take_profit:
                order_id = market_orders(symbol=symbol,quantity=abs(positionAmt),side='BUY')

                if order_id != None:
                    logging.info(f'{symbol}止盈成功')
            #止盈
            elif new_price >=stop_loss:
                order_id = market_orders(symbol=symbol,quantity=abs(positionAmt),side='BUY')

                if order_id != None:
                    logging.info(f'{symbol}止损成功')


if __name__ == "__main__":
    
    #初始化交易所
    client = UMFutures(key=api_key,secret=api_secret)

    # 初始化DeepSeek客户端
    deepseek_client = OpenAI(
        api_key=ds_api,
        base_url="https://api.deepseek.com"
    )

    #获取下单数量精度
    quantity_precision = get_symbol_info(symbol)

    #获取最大杠杠倍数
    lv = get_leverage(symbol)
    if leverage < lv:
        #设置杠杠倍数
        change_leverage(symbol,leverage)
    else:
        change_leverage(symbol,lv)

    #获取symbols的历史K线数据
    historical_klines = get_historical_klines(symbol, interval,start_time,end_time)
    klines_to_dataframe(historical_klines)

    #订阅行情数据
    all_streams = []
    # 所有 symbol 和 data_type 的组合
    for data_type in market_data:
        stream = f"{symbol.lower()}@{data_type}"
        all_streams.append(stream)

    market = MarketDataWebSocket(streams=all_streams)
    market.start()

    user = UserDataWebSocket()
    user.start()

    t1 = threading.Thread(target=ds,daemon=True)
    t1.start()

    t2 = threading.Thread(target=open_trade,daemon=True)
    t2.start()

    t3 = threading.Thread(target=close_trade,daemon=True)
    t3.start()

    try:
        while True:
            time.sleep(60 * 58)
            # 延长 listenKey（注意：t.listenKey）
            client.renew_listen_key(user.listenKey)
    except KeyboardInterrupt:
        logging.info("主线程检测到退出信号，程序终止")

