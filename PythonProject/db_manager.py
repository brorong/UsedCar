import sqlite3
import datetime

DB_NAME = 'car_listings_v2.db'  # 🔥 啟用全新的 V2 資料庫，避免與舊版衝突


def init_db():
    """初始化雙軌制資料庫：車籍總表 (cars_master) 與 價格日誌表 (price_history)"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # 1. 車籍總表：記錄車輛基本不變的資訊與上下架狀態
    cursor.execute('''
                   CREATE TABLE IF NOT EXISTS cars_master
                   (
                       global_car_id
                       TEXT
                       PRIMARY
                       KEY,     -- 格式：platform_car_id (例: 8891_12345)
                       platform
                       TEXT
                       NOT
                       NULL,
                       brand
                       TEXT
                       NOT
                       NULL,
                       model
                       TEXT
                       NOT
                       NULL,
                       year
                       INTEGER,
                       mileage
                       INTEGER,
                       location
                       TEXT,
                       url
                       TEXT
                       NOT
                       NULL,
                       first_seen
                       DATE
                       NOT
                       NULL,    -- 首次上架日
                       last_seen
                       DATE
                       NOT
                       NULL,    -- 最後掃描日
                       status
                       TEXT
                       DEFAULT
                       'online' -- 狀態：online (上架中) / offline (已下架)
                   )
                   ''')

    # 2. 價格日誌表：記錄每一次的價格變動
    cursor.execute('''
                   CREATE TABLE IF NOT EXISTS price_history
                   (
                       id
                       INTEGER
                       PRIMARY
                       KEY
                       AUTOINCREMENT,
                       global_car_id
                       TEXT
                       NOT
                       NULL,
                       record_date
                       DATE
                       NOT
                       NULL,
                       price
                       INTEGER,
                       price_type
                       TEXT,
                       FOREIGN
                       KEY
                   (
                       global_car_id
                   ) REFERENCES cars_master
                   (
                       global_car_id
                   )
                       )
                   ''')

    # 建立索引以加快查詢速度
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_status ON cars_master(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_last_seen ON cars_master(last_seen)')

    conn.commit()
    conn.close()
    print(f"[資料庫總管] {DB_NAME} 雙軌制資料庫初始化完成！")


def update_listings(platform_name, scraped_cars):
    """
    核心數據樞紐：接收爬蟲資料，進行 新增、變價、下架 的判定
    """
    if not scraped_cars:
        print(f"[{platform_name.upper()}] 無資料傳入，跳過更新。")
        return

    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    new_cars_count = 0
    price_changed_count = 0
    updated_count = 0

    print(f"\n[{platform_name.upper()}] 開始進行籌碼流動分析與寫入...")

    for car in scraped_cars:
        # 確保全局 ID 唯一性
        global_id = f"{platform_name}_{car['car_id']}"
        current_price = car['price']
        current_type = car['price_type']

        # 1. 檢查這台車是否已存在於總表
        cursor.execute("SELECT status FROM cars_master WHERE global_car_id = ?", (global_id,))
        row = cursor.fetchone()

        if not row:
            # 🟢 狀況 A：這是一台全新上架的車
            cursor.execute('''
                           INSERT INTO cars_master
                           (global_car_id, platform, brand, model, year, mileage, location, url, first_seen, last_seen,
                            status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'online')
                           ''', (global_id, platform_name, car['brand'], car['model'], car['year'], car['mileage'],
                                 car['location'], car['url'], today_str, today_str))

            # 寫入第一筆價格紀錄
            cursor.execute('''
                           INSERT INTO price_history (global_car_id, record_date, price, price_type)
                           VALUES (?, ?, ?, ?)
                           ''', (global_id, today_str, current_price, current_type))

            new_cars_count += 1

        else:
            # 🟡 狀況 B：這台車之前抓過，更新最後存活日期並標記為上架
            cursor.execute('''
                           UPDATE cars_master
                           SET last_seen = ?,
                               status    = 'online',
                               mileage   = ?,
                               location  = ?
                           WHERE global_car_id = ?
                           ''', (today_str, car['mileage'], car['location'], global_id))

            # 檢查價格是否有變動 (撈取該車最新的一筆價格紀錄)
            cursor.execute('''
                           SELECT price, price_type
                           FROM price_history
                           WHERE global_car_id = ?
                           ORDER BY record_date DESC, id DESC LIMIT 1
                           ''', (global_id,))
            last_price_row = cursor.fetchone()

            # 如果價格不同，或是從電洽變成有標價，就新增一筆變價紀錄
            if last_price_row and (last_price_row[0] != current_price or last_price_row[1] != current_type):
                cursor.execute('''
                               INSERT INTO price_history (global_car_id, record_date, price, price_type)
                               VALUES (?, ?, ?, ?)
                               ''', (global_id, today_str, current_price, current_type))
                price_changed_count += 1

            updated_count += 1

    # 🔴 狀況 C：自動判定下架
    # 如果資料庫裡有該平台的車，且狀態是 online，但今天卻沒有掃描到 (last_seen != today_str)
    # 代表這台車從網頁上消失了，標記為 offline
    cursor.execute('''
                   UPDATE cars_master
                   SET status = 'offline'
                   WHERE platform = ?
                     AND status = 'online'
                     AND last_seen != ?
                   ''', (platform_name, today_str))

    delisted_count = cursor.rowcount

    conn.commit()
    conn.close()

    # 印出戰情報告
    print(f"  └ 🟢 新增上架: {new_cars_count} 台")
    print(f"  └ 🟡 偵測變價: {price_changed_count} 台")
    print(f"  └ ⚪ 維持現狀: {updated_count - price_changed_count} 台")
    print(f"  └ 🔴 發現下架: {delisted_count} 台 (已從網頁消失)")
    print(f"[{platform_name.upper()}] 籌碼流動更新完畢！\n")


# 測試用區塊 (獨立執行此檔案時會建立資料庫)
if __name__ == "__main__":
    init_db()