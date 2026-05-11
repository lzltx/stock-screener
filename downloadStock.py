# downloadStock.py (GitHub Actions 版，含精确周线)
import akshare as ak
import pandas as pd
import json
from datetime import datetime, timedelta
import sys

def calc_macd(df, fast=12, slow=26, signal=9):
    """为DataFrame添加 mac_dif, mac_dea, mac_hist"""
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
    elif code.startswith('400','420','430','830'):
        return '北交所/老三板'
    else:
        return '其他'

def get_industry():
    """构建 代码→行业 字典（简化版，取首次出现的行业）"""
    ind_map = {}
    try:
        df_ind = ak.stock_board_industry_name_em()
        for ind in df_ind['板块名称'].tolist():
            try:
                cons = ak.stock_board_industry_cons_em(symbol=ind)
                for c in cons['代码']:
                    if c not in ind_map:
                        ind_map[c] = ind
            except:
                continue
    except:
        print("获取行业信息失败")
    return ind_map

def main():
    print("开始采集数据...")
    # 1. 股票基本信息
    spot = ak.stock_zh_a_spot_em()
    spot = spot.rename(columns={'代码':'code','名称':'name','总市值':'total_market_cap'})
    spot = spot[['code','name','total_market_cap']]
    spot['total_market_cap'] = pd.to_numeric(spot['total_market_cap'], errors='coerce')
    
    # 行业和市场
    industry_map = get_industry()
    spot['industry'] = spot['code'].map(industry_map)
    spot['market'] = spot['code'].apply(get_market)
    
    # 2. 获取日线和周线数据（使用两年日线聚合周线）
    day_start = (datetime.now() - timedelta(days=400)).strftime('%Y%m%d')    # 约一年半，足够周线
    day_end = datetime.now().strftime('%Y%m%d')
    
    records = []
    total = len(spot)
    for idx, row in spot.iterrows():
        code = row['code']
        try:
            # ----- 日线 -----
            df_day = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=day_start, end_date=day_end, adjust="qfq")
            if df_day.empty:
                continue
            # 统一列名
            col_map = {'日期':'trade_date','开盘':'open','收盘':'close','最高':'high','最低':'low','成交量':'volume'}
            df_day.rename(columns=col_map, inplace=True)
            df_day[['open','high','low','close','volume']] = df_day[['open','high','low','close','volume']].astype(float)
            df_day['trade_date'] = pd.to_datetime(df_day['trade_date'])
            df_day = calc_macd(df_day)
            
            last_day = df_day.iloc[-1].to_dict()
            # 前一日
            if len(df_day) >= 2:
                prev_day = df_day.iloc[-2]
                last_day['prev_mac_dif'] = prev_day['mac_dif']
                last_day['prev_mac_dea'] = prev_day['mac_dea']
                last_day['prev_mac_hist'] = prev_day['mac_hist']
            else:
                last_day['prev_mac_dif'] = None
                last_day['prev_mac_dea'] = None
                last_day['prev_mac_hist'] = None
            
            # ----- 周线 -----
            df_day_indexed = df_day.set_index('trade_date')
            # 重采样为周（取周五，若周五无交易则取最近交易日）
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
                # 无周线数据
                last_week = {'trade_date': None, 'close': None, 'mac_dif': None, 'mac_dea': None, 'mac_hist': None,
                             'prev_week_mac_dif': None, 'prev_week_mac_dea': None, 'prev_week_mac_hist': None}
            
            # 合并记录
            rec = {
                'code': code,
                'name': row['name'],
                'industry': row['industry'],
                'market': row['market'],
                'totalMarketCap': row['total_market_cap'],
                'tradeDate': last_day['trade_date'].strftime('%Y-%m-%d') if hasattr(last_day['trade_date'], 'strftime') else str(last_day['trade_date'])[:10],
                'close': last_day['close'],
                'macDif': last_day['mac_dif'],
                'macDea': last_day['mac_dea'],
                'macHist': last_day['mac_hist'],
                'prevMacDif': last_day['prev_mac_dif'],
                'prevMacDea': last_day['prev_mac_dea'],
                'prevMacHist': last_day['prev_mac_hist'],
                'weekTradeDate': last_week['trade_date'].strftime('%Y-%m-%d') if last_week['trade_date'] and hasattr(last_week['trade_date'], 'strftime') else str(last_week['trade_date'])[:10] if last_week['trade_date'] else None,
                'weekClose': last_week['close'],
                'weekMacDif': last_week['mac_dif'],
                'weekMacDea': last_week['mac_dea'],
                'weekMacHist': last_week['mac_hist'],
                'prevWeekMacDif': last_week['prev_week_mac_dif'],
                'prevWeekMacDea': last_week['prev_week_mac_dea'],
                'prevWeekMacHist': last_week['prev_week_mac_hist']
            }
            records.append(rec)
        except Exception as e:
            print(f"处理 {code} 失败: {e}")
            continue
        if (idx+1) % 100 == 0:
            print(f"进度 {idx+1}/{total}")
    
    with open('stocks.json', 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, default=str)
    print(f"更新完成，共 {len(records)} 只股票")

if __name__ == '__main__':
    main()