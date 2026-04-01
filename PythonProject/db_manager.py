import sqlite3
import datetime

DB_NAME = 'car_listings_v2.db'  # 🔥 啟用全新的 V2 資料庫

def init_db():
    """初始化雙軌制資料庫：車籍總表 (cars_master) 與 價格日誌表 (price_history)"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # 1. 車籍總表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cars_master (
            global_car_id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            brand TEXT NOT NULL,
            model TEXT NOT NULL,
            year INTEGER,
            mileage INTEGER,
            location TEXT,
            url TEXT NOT NULL,
            first_seen DATE NOT NULL,
            last_seen DATE NOT NULL,
            status TEXT DEFAULT 'online'
        )
    ''')

    # 2. 價格日誌表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            global_car_id TEXT NOT NULL,
            record_date DATE NOT NULL,
            price INTEGER,
            price_type TEXT,
            FOREIGN KEY (global_car_id) REFERENCES cars_master(global_car_id)
        )
    ''')

    # 建立索引以加快查詢速度
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_status ON cars_master(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_last_seen ON cars_master(last_seen)')

    conn.commit()
    conn.close()


def update_listings(platform_name, scraped_cars):
    """
    核心數據樞紐：接收爬蟲資料，進行 新增、變價、下架 的判定
    """
    # 強制平台名稱大寫，防止大小寫比對失誤
    platform_name = platform_name.upper()

    if not scraped_cars:
        print(f"[{platform_name}] 無資料傳入，跳過更新。")
        return

    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    new_cars_count = 0
    price_changed_count = 0
    updated_count = 0

    print(f"\n[{platform_name}] 開始進行籌碼流動分析與寫入...")

    # 收集這次抓到的所有 ID，用於後續精準比對下架
    current_scraped_ids = []

    for car in scraped_cars:
        # 防呆機制：兼容不同爬蟲的 ID 寫法
        global_id = car.get('global_car_id')
        if not global_id:
            global_id = f"{platform_name}_{car.get('car_id', '')}"
        
        current_scraped_ids.append(global_id)

        current_price = car.get('price')
        current_type = car.get('price_type', 'normal')

        # 1. 檢查這台車是否已存在於總表
        cursor.execute("SELECT status FROM cars_master WHERE global_car_id = ?", (global_id,))
        row = cursor.fetchone()

        if not row:
            # 🟢 狀況 A：這是一台全新上架的車
            cursor.execute('''
                INSERT INTO cars_master 
                (global_car_id, platform, brand, model, year, mileage, location, url, first_seen, last_seen, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'online')
            ''', (global_id, platform_name, car.get('brand', '').upper(), car.get('model', '').upper(), 
                  car.get('year', 0), car.get('mileage', 0), car.get('location', '未知'), 
                  car.get('url', ''), today_str, today_str))

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
            ''', (today_str, car.get('mileage', 0), car.get('location', '未知'), global_id))

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

    # 🔴 狀況 C：自動判定下架 (終極嚴格版)
    # 利用 NOT IN 排除今天有掃描到的車輛，剩下的就是下架車輛
    if current_scraped_ids:
        placeholders = ','.join(['?'] * len(current_scraped_ids))
        query = f'''
            UPDATE cars_master
            SET status = 'offline'
            WHERE platform = ?
              AND status = 'online'
              AND global_car_id NOT IN ({placeholders})
        '''
        cursor.execute(query, [platform_name] + current_scraped_ids)
        delisted_count = cursor.rowcount
    else:
        delisted_count = 0

    conn.commit()
    conn.close()

    # 印出戰情報告
    print(f"  └ 🟢 新增上架: {new_cars_count} 台")
    print(f"  └ 🟡 偵測變價: {price_changed_count} 台")
    print(f"  └ ⚪ 維持現狀: {updated_count - price_changed_count} 台")
    print(f"  └ 🔴 發現下架: {delisted_count} 台 (已從網頁消失)")
    print(f"[{platform_name}] 籌碼流動更新完畢！\n")


# 測試用區塊
if __name__ == "__main__":
    init_db()
