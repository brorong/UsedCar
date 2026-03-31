"""
line_notify_n7.py
Luxgen N7 專屬 LINE 推播模板 (新增電洽/無價格統計)
參考既有 car_listings_v2.db 資料結構
執行：python line_notify_n7.py
"""

import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

import sqlite3
import datetime
import requests
from collections import defaultdict
from typing import List, Dict, Optional

# ══════════════════════════════════════════════════════════════════════════════
#  ⚙️  設定區
# ══════════════════════════════════════════════════════════════════════════════
DB_NAME = "car_listings_v2.db"
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_TOKEN", "您的備用TOKEN")
LINE_USER_ID = os.getenv("LINE_GROUP_ID", "您的群組ID")
LINE_MSG_LIMIT = 4500

TARGET_BRAND = "LUXGEN"
TARGET_MODEL_LIKE = "N7%"

PLATFORM_NAMES = {
    "8891": "8891",
    "save": "SAVE",
    "hot":  "HOT",
    "sum":  "SUM",
    "abc":  "ABC",
    "luxlife": "原廠",
}

# 價格區間（萬元）
PRICE_BANDS = [
    (0,    60,   "60萬以下"),
    (60,   70,   "60–70萬"),
    (70,   80,   "70–80萬"),
    (80,   90,   "80–90萬"),
    (90,   100,  "90–100萬"),
    (100,  999,  "100萬以上"),
]


# ══════════════════════════════════════════════════════════════════════════════
#  🗄️  資料查詢
# ══════════════════════════════════════════════════════════════════════════════
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def fetch_n7_data() -> Dict:
    today   = datetime.date.today()
    today_s = today.strftime("%Y-%m-%d")
    w3_s    = (today - datetime.timedelta(days=3)).strftime("%Y-%m-%d")

    conn = get_conn()
    cur  = conn.cursor()

    # ── 1. 上架總量 ──
    cur.execute("""
        SELECT platform, COUNT(*) AS cnt
        FROM cars_master
        WHERE status = 'online' AND UPPER(brand) = ? AND UPPER(model) LIKE ?
        GROUP BY platform ORDER BY cnt DESC
    """, (TARGET_BRAND, TARGET_MODEL_LIKE))
    stock_by_platform = [dict(r) for r in cur.fetchall()]
    total_stock = sum(r["cnt"] for r in stock_by_platform)

    # ── 2. 今日新上架 ──
    cur.execute("""
        SELECT platform, COUNT(*) AS cnt
        FROM cars_master
        WHERE UPPER(brand) = ? AND UPPER(model) LIKE ? AND first_seen = ?
        GROUP BY platform
    """, (TARGET_BRAND, TARGET_MODEL_LIKE, today_s))
    new_today_rows = [dict(r) for r in cur.fetchall()]
    new_today = sum(r["cnt"] for r in new_today_rows)

    # ── 3. 今日下架 ──
    cur.execute("""
        SELECT platform, COUNT(*) AS cnt
        FROM cars_master
        WHERE status = 'offline' AND UPPER(brand) = ? AND UPPER(model) LIKE ? AND last_seen >= ?
        GROUP BY platform
    """, (TARGET_BRAND, TARGET_MODEL_LIKE, w3_s))
    delisted_rows = [dict(r) for r in cur.fetchall()]
    delisted = sum(r["cnt"] for r in delisted_rows)

    # ── 4. 今日降價 ──
    cur.execute("""
        WITH Ranked AS (
            SELECT global_car_id, price, ROW_NUMBER() OVER (PARTITION BY global_car_id ORDER BY record_date DESC) AS rn
            FROM price_history WHERE price IS NOT NULL AND price > 0
        ),
        Diffs AS (
            SELECT p1.global_car_id, p1.price AS latest_price, (p1.price - p2.price) AS diff
            FROM Ranked p1 JOIN Ranked p2 ON p1.global_car_id = p2.global_car_id AND p2.rn = 2
            WHERE p1.rn = 1 AND (p1.price - p2.price) < 0
        )
        SELECT c.platform, c.year, c.location, c.url, d.latest_price, d.diff
        FROM cars_master c JOIN Diffs d ON c.global_car_id = d.global_car_id
        WHERE c.status = 'online' AND UPPER(c.brand) = ? AND UPPER(c.model) LIKE ?
        ORDER BY d.diff ASC LIMIT 5
    """, (TARGET_BRAND, TARGET_MODEL_LIKE))
    price_drops = [dict(r) for r in cur.fetchall()]

    # ── 5. 價格、年式、里程原始資料 ──
    cur.execute("""
        WITH Latest AS (
            SELECT global_car_id, price, ROW_NUMBER() OVER (PARTITION BY global_car_id ORDER BY record_date DESC) AS rn
            FROM price_history WHERE price IS NOT NULL AND price > 0
        )
        SELECT l.price, c.year, c.mileage
        FROM cars_master c
        JOIN Latest l ON c.global_car_id = l.global_car_id AND l.rn = 1
        WHERE c.status = 'online' AND UPPER(c.brand) = ? AND UPPER(c.model) LIKE ?
    """, (TARGET_BRAND, TARGET_MODEL_LIKE))
    raw_details = [dict(r) for r in cur.fetchall()]
    prices_raw = [r["price"] for r in raw_details]

    # ── 6. 地區分布 ──
    cur.execute("""
        SELECT location, COUNT(*) AS cnt
        FROM cars_master
        WHERE status = 'online' AND UPPER(brand) = ? AND UPPER(model) LIKE ?
        GROUP BY location ORDER BY cnt DESC LIMIT 5
    """, (TARGET_BRAND, TARGET_MODEL_LIKE))
    region_rows = [dict(r) for r in cur.fetchall()]

    conn.close()

    return {
        "today":             today_s,
        "total_stock":       total_stock,
        "stock_by_platform": stock_by_platform,
        "new_today":         new_today,
        "new_today_rows":    new_today_rows,
        "delisted":          delisted,
        "delisted_rows":     delisted_rows,
        "price_drops":       price_drops,
        "prices_raw":        prices_raw,
        "raw_details":       raw_details,
        "region_rows":       region_rows,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  📊  數據計算與視覺化
# ══════════════════════════════════════════════════════════════════════════════
def build_price_distribution(prices_raw: List[int]) -> List[Dict]:
    result = []
    for lo, hi, label in PRICE_BANDS:
        lo_n, hi_n = lo * 10000, hi * 10000
        cnt = sum(1 for p in prices_raw if lo_n <= p < hi_n)
        if cnt > 0 or lo == 0:
            result.append({"label": label, "count": cnt})
    return result

def bar_chart(count: int, total: int, width: int = 10) -> str:
    if total == 0: return "░" * width
    filled = round(count / total * width)
    return "█" * filled + "░" * (width - filled)


# ══════════════════════════════════════════════════════════════════════════════
#  📋  推播訊息組裝
# ══════════════════════════════════════════════════════════════════════════════
def build_message(data: Dict) -> str:
    today        = data["today"]
    total        = data["total_stock"]
    new_today    = data["new_today"]
    delisted     = data["delisted"]
    price_drops  = data["price_drops"]
    prices_raw   = data["prices_raw"]
    raw_details  = data["raw_details"]
    regions      = data["region_rows"]

    avg_price = int(sum(prices_raw) / len(prices_raw) / 10000) if prices_raw else 0
    min_price = int(min(prices_raw) / 10000) if prices_raw else 0
    max_price = int(max(prices_raw) / 10000) if prices_raw else 0

    price_dist = build_price_distribution(prices_raw)

    # 【新增】：計算無價格車輛數 (總數 - 有標價車輛數)
    no_price_count = total - len(prices_raw)

    net_change = new_today - delisted
    if net_change > 3: market_signal = "📈 供給增加"
    elif net_change < -3: market_signal = "📉 去化加速"
    else: market_signal = "➡️  市場平穩"

    lines = [f"🚗 Luxgen N7 車系 每日戰情報", f"📅 {today}", "━" * 20, ""]

    # 一、上架總量
    lines += ["【 📦 上架總量 】", f"全平台合計：{total} 台  {market_signal}", ""]
    for r in data["stock_by_platform"]:
        plat = PLATFORM_NAMES.get(r["platform"].lower(), r["platform"].upper())
        pct  = round(r["cnt"] / total * 100) if total else 0
        lines.append(f"  {plat:<6} {r['cnt']:>3} 台  ({pct}%)")
    lines.append("")

    # 二、今日異動
    lines += ["【 🔄 今日異動 】", f"  新上架：＋{new_today} 台"]
    if data["new_today_rows"]:
        for r in data["new_today_rows"]:
            plat = PLATFORM_NAMES.get(r["platform"].lower(), r["platform"].upper())
            lines.append(f"    └ {plat} ＋{r['cnt']} 台")
    lines.append(f"  下  架：－{delisted} 台（推測成交）")
    if data["delisted_rows"]:
        for r in data["delisted_rows"]:
            plat = PLATFORM_NAMES.get(r["platform"].lower(), r["platform"].upper())
            lines.append(f"    └ {plat} －{r['cnt']} 台")
    net_sign = "＋" if net_change >= 0 else "－"
    lines += [f"  淨異動：{net_sign}{abs(net_change)} 台", ""]

    # 三、價格分佈
    lines += ["【 💰 價格分佈 】", f"  均價 {avg_price} 萬  |  區間 {min_price}–{max_price} 萬", ""]
    for band in price_dist:
        bar = bar_chart(band["count"], total, width=8)
        lines.append(f"  {band['label']:<10} {bar} {band['count']}台")

    # 【新增關鍵備註】
    if no_price_count > 0:
        lines.append(f"  (另有 {no_price_count} 台價格不詳/電洽)")
    lines.append("")

    # 四、年式與里程
    year_stats = defaultdict(lambda: {"cnt": 0, "total_price": 0})
    mileage_bands = {"1萬km內": 0, "1～2萬km": 0, "2～3萬km": 0, "3萬km以上": 0}
    total_mileage, valid_mileage_cnt = 0, 0
    for r in raw_details:
        y, p, m = r["year"], r["price"], r["mileage"]
        if y and y > 2000:
            year_stats[y]["cnt"] += 1
            year_stats[y]["total_price"] += p
        if m is not None and m >= 0:
            total_mileage += m
            valid_mileage_cnt += 1
            if m < 10000: mileage_bands["1萬km內"] += 1
            elif m < 20000: mileage_bands["1～2萬km"] += 1
            elif m < 30000: mileage_bands["2～3萬km"] += 1
            else: mileage_bands["3萬km以上"] += 1
    avg_mileage = (total_mileage / valid_mileage_cnt / 10000) if valid_mileage_cnt else 0

    lines += ["【 📅 年式行情與里程 】"]
    for y in sorted(year_stats.keys(), reverse=True):
        stat = year_stats[y]
        y_avg = stat["total_price"] / stat["cnt"] / 10000
        lines.append(f"  {y}年：{stat['cnt']:>2}台 | 均價 {y_avg:.1f} 萬")
    lines.append(f"\n  🛣️ 市場平均里程：{avg_mileage:.1f} 萬公里")
    for label, count in mileage_bands.items():
        if count > 0:
            bar = bar_chart(count, valid_mileage_cnt, width=6)
            lines.append(f"  {label:<8} {bar} {count}台")
    lines.append("")

    # 五、降價雷達
    if price_drops:
        lines += ["【 🚨 降價雷達 Top5 】"]
        for d in price_drops:
            plat = PLATFORM_NAMES.get(d["platform"].lower(), d["platform"].upper())
            diff_wan, price_wan = abs(d["diff"]) / 10000, d["latest_price"] / 10000
            region = (d["location"] or "")[:3]
            lines += [f"  🔻 {d['year']}年 {region}  [{plat}]", f"     砍 {diff_wan:.1f}萬 ➜ 售 {price_wan:.1f}萬", f"     {d['url']}", ""]
    else:
        lines += ["【 🚨 降價雷達 】", "  本日無 N7 降價紀錄", ""]

    # 六、地區分布
    if regions:
        lines += ["【 📍 主要上架地區 】"]
        for r in regions:
            region = (r["location"] or "未知")[:4]
            bar = bar_chart(r["cnt"], total, width=6)
            lines.append(f"  {region:<5} {bar} {r['cnt']}台")
        lines.append("")

    lines += ["━" * 20, f"🤖 自動推播  |  N7 專屬監控"]
    return "\n".join(lines)


def send_line(message: str):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print(message); return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    for i in range(0, len(message), LINE_MSG_LIMIT):
        chunk = message[i : i + LINE_MSG_LIMIT]
        payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": chunk}]}
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code != 200: print(f"LINE 發送失敗：{resp.status_code} {resp.text}")
    print("✅ N7 戰情報推播完成！")


if __name__ == "__main__":
    try:
        data = fetch_n7_data()
        message = build_message(data)
        print("=" * 40 + "\n" + message + "\n" + "=" * 40)
        send_line(message) # 測試成功後取消註解
    except Exception as e:
        print(f"❌ 系統錯誤：{e}"); raise
