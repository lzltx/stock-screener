# downloadStock.py (带重试和限流保护)
import akshare as ak
import pandas as pd
import json
import time
import random
import sys
from datetime import datetime, timedelta

# ---------- 网络请求封装 ----------
def safe_request(func, *args, max_retries=3, **kwargs):
    """指数退避重试，防止被 Ban"""
    for attempt in range(1, max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(f"请求失败 (尝试 {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                wait = 5 * (2 ** (attempt - 1))   # 5,10,20 秒
                print(f"等待 {wait} 秒后重试...")
                time.sleep(wait)
            else:
                raise

# ---------- 指标计算 ----------
def calc_macd(df, fast=12, slow=26, signal=9):
    df = df.sort_values('trade_date')
    close = df['close'].astype(float)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = 2 * (dif - dea)
    df['mac_dif'] = dif
    df['mac_dea'] = dea
    df['mac_hist'] = hist
    return df

def get_market(code):
    code = str(code).zfill(6)
    if code.startswith(('600','601','603','605')):
        return '上海主板'
    elif code.startswith('688'):
        return '科创板'
    elif code.startswith(('000','001','002','003')):
        return '深圳主板'
    elif code.startswith('300'):
        return '创业板'
    elif code.startswith(('400','420','430','830')):
        return '北交所/老三板'
    else:
        return '其他'

def get_industry():
    """获取股票代码 -> 行业字典"""
    ind_map = {}
    try:
        df_ind = safe_request(ak.stock_board_industry_name_em)
        industries = df_ind['板块名称'].tolist()
        for idx, ind in enumerate(industries, 1):
            try:
                cons = safe_request(ak.stock_board_industry_cons_em, symbol=ind)
                for c in cons['代码']:
                    if c not in ind_map:
                        ind_map[c] = ind
                time.sleep(random.uniform(0.5, 1.5))  # 降低频率
            except:
                continue
            if idx % 10 == 0:
                print(f"行业进度 {idx}/{len(industries)}")
    except Exception as e:
        print(f"获取行业失败: {e}")
    return ind_map

# ---------- 主流程 ----------
def main():
    print("开始采集数据...")
    
    # 1. 基本信息
    print("获取股票列表...")
    spot = safe_request(ak.stock_zh_a_spot_em)
    spot = spot.rename(columns={'代码':'code','名称':'name','总市值':'total_market_cap'})
    spot = spot[['code','name','total_market_cap']]
    spot['total_market_cap'] = pd.to_numeric(spot['total_market_cap'], errors='coerce')
    
    # 行业与市场
    print("获取行业数据...")
    industry_map = get_industry()
    spot['industry'] = spot['code'].map(industry_map)
    spot['market'] = spot['code'].apply(get_market)
    
    # 时间范围
    day_end = datetime.now().strftime('%Y%m%d')
    day_start = (datetime.now() - timedelta(days=400)).strftime('%Y%m%d')
    
    records = []
    total = len(spot)
    print(f"开始处理 {total} 只股票...")
    for idx, (_, row) in enumerate(spot.iterrows()):
        code = row['code']
        try:
            # ---------- 日线 ----------
            df_day = safe_request(ak.stock_zh_a_hist, symbol=code, period="daily",
                                  start_date=day_start, end_date=day_end, adjust="qfq")
            if df_day.empty:
                continue
            col_map = {'日期':'trade_date','开盘':'open','收盘':'close','最高':'high','最低':'low','成交量':'volume'}
            df_day.rename(columns=col_map, inplace=True)
            for col in ['open','high','low','close','volume']:
                df_day[col] = pd.to_numeric(df_day[col], errors='coerce')
            df_day['trade_date'] = pd.to_datetime(df_day['trade_date'])
            df_day = calc_macd(df_day)
            
            last_day = df_day.iloc[-1].to_dict()
            if len(df_day) >= 2:
                prev_day = df_day.iloc[-2]
                last_day['prev_mac_dif'] = prev_day['mac_dif']
                last_day['prev_mac_dea'] = prev_day['mac_dea']
                last_day['prev_mac_hist'] = prev_day['mac_hist']
            else:
                last_day['prev_mac_dif'] = last_day['prev_mac_dea'] = last_day['prev_mac_hist'] = None
            
            # ---------- 周线 ----------
            df_day_indexed = df_day.set_index('trade_date')
            weekly = df_day_indexed.resample('W-FRI').agg({
                'open':'first','high':'max','low':'min','close':'last','volume':'sum'
            }).dropna()
            weekly = weekly.reset_index()
            weekly = calc_macd(weekly)
            
            if len(weekly) >= 2:
                last_week = weekly.iloc[-1].to_dict()
                prev_week = weekly.iloc[-2]
                last_week['prev_week_mac_dif'] = prev_week['mac_dif']
                last_week['prev_week_mac_dea'] = prev_week['mac_dea']
                last_week['prev_week_mac_hist'] = prev_week['mac_hist']
            elif len(weekly) == 1:
                last_week = weekly.iloc[-1].to_dict()
                last_week['prev_week_mac_dif'] = None
                last_week['prev_week_mac_dea'] = None
                last_week['prev_week_mac_hist'] = None
            else:
                last_week = {'trade_date': None, 'close': None, 'mac_dif': None, 'mac_dea': None, 'mac_hist': None,
                             'prev_week_mac_dif': None, 'prev_week_mac_dea': None, 'prev_week_mac_hist': None}
            
            rec = {
                'code': code,
                'name': row['name'],
                'industry': row['industry'],
                'market': row['market'],
                'totalMarketCap': row['total_market_cap'],
                'tradeDate': last_day['trade_date'].strftime('%Y-%m-%d') if isinstance(last_day['trade_date'], pd.Timestamp) else str(last_day['trade_date'])[:10],
                'close': last_day['close'],
                'macDif': last_day['mac_dif'],
                'macDea': last_day['mac_dea'],
                'macHist': last_day['mac_hist'],
                'prevMacDif': last_day['prev_mac_dif'],
                'prevMacDea': last_day['prev_mac_dea'],
                'prevMacHist': last_day['prev_mac_hist'],
                'weekTradeDate': last_week['trade_date'].strftime('%Y-%m-%d') if last_week['trade_date'] and isinstance(last_week['trade_date'], pd.Timestamp) else str(last_week['trade_date'])[:10] if last_week['trade_date'] else None,
                'weekClose': last_week['close'],
                'weekMacDif': last_week['mac_dif'],
                'weekMacDea': last_week['mac_dea'],
                'weekMacHist': last_week['mac_hist'],
                'prevWeekMacDif': last_week['prev_week_mac_dif'],
                'prevWeekMacDea': last_week['prev_week_mac_dea'],
                'prevWeekMacHist': last_week['prev_week_mac_hist']
            }
            records.append(rec)
            
            # 控制请求速度
            time.sleep(random.uniform(0.3, 0.8))
            if (idx+1) % 50 == 0:
                print(f"进度 {idx+1}/{total}")
        except Exception as e:
            print(f"处理 {code} 失败: {e}")
            time.sleep(2)
            continue
    
    with open('stocks.json', 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, default=str)
    print(f"更新完成，共 {len(records)} 只股票")

if __name__ == '__main__':
    main()
