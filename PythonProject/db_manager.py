import sqlite3
import datetime
import os

# 🔥 核心修正：綁定絕對路徑
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_NAME = os.path.join(BASE_DIR, 'car_listings_v2.db')

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

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_status ON cars_master(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_last_seen ON cars_master(last_seen)')

    conn.commit()
    conn.close()


def update_listings(platform_name, scraped_cars):
    """核心數據樞紐：接收爬蟲資料，進行 新增、變價、下架 的判定"""
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
    delisted_count = 0

    print(f"\n[{platform_name}] 開始進行籌碼流動分析與寫入...")
    current_scraped_ids = []

    try:
        for car in scraped_cars:
            global_id = car.get('global_car_id')
            if not global_id:
                global_id = f"{platform_name}_{car.get('car_id', '')}"
            
            current_scraped_ids.append(global_id)

            current_price = car.get('price')
            current_type = car.get('price_type', 'normal')

            cursor.execute("SELECT status FROM cars_master WHERE global_car_id = ?", (global_id,))
            row = cursor.fetchone()

            if not row:
                cursor.execute('''
                    INSERT INTO cars_master 
                    (global_car_id, platform, brand, model, year, mileage, location, url, first_seen, last_seen, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'online')
                ''', (global_id, platform_name, car.get('brand', '').upper(), car.get('model', '').upper(), 
                      car.get('year', 0), car.get('mileage', 0), car.get('location', '未知'), 
                      car.get('url', ''), today_str, today_str))

                cursor.execute('''
                    INSERT INTO price_history (global_car_id, record_date, price, price_type)
                    VALUES (?, ?, ?, ?)
                ''', (global_id, today_str, current_price, current_type))
                new_cars_count += 1

            else:
                cursor.execute('''
                    UPDATE cars_master
                    SET last_seen = ?, status = 'online', mileage = ?, location = ?
                    WHERE global_car_id = ?
                ''', (today_str, car.get('mileage', 0), car.get('location', '未知'), global_id))

                cursor.execute('''
                    SELECT price, price_type FROM price_history
                    WHERE global_car_id = ? ORDER BY record_date DESC, id DESC LIMIT 1
                ''', (global_id,))
                last_price_row = cursor.fetchone()

                if last_price_row and (last_price_row[0] != current_price or last_price_row[1] != current_type):
                    cursor.execute('''
                        INSERT INTO price_history (global_car_id, record_date, price, price_type)
                        VALUES (?, ?, ?, ?)
                    ''', (global_id, today_str, current_price, current_type))
                    price_changed_count += 1

                updated_count += 1

        if current_scraped_ids:
            placeholders = ','.join(['?'] * len(current_scraped_ids))
            query = f'''
                UPDATE cars_master
                SET status = 'offline'
                WHERE platform = ? AND status = 'online' AND global_car_id NOT IN ({placeholders})
            '''
            cursor.execute(query, [platform_name] + current_scraped_ids)
            delisted_count = cursor.rowcount

        conn.commit()

    except Exception as e:
        print(f"❌ 寫入資料庫時發生嚴重錯誤: {e}")
        conn.rollback()
    finally:
        conn.close()

    print(f"  └ 🟢 新增上架: {new_cars_count} 台")
    print(f"  └ 🟡 偵測變價: {price_changed_count} 台")
    print(f"  └ ⚪ 維持現狀: {updated_count - price_changed_count} 台")
    print(f"  └ 🔴 發現下架: {delisted_count} 台 (已從網頁消失)")
    print(f"[{platform_name}] 籌碼流動更新完畢！\n")

if __name__ == "__main__":
    init_db()
