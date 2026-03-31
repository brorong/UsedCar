import sqlite3
import datetime
import time
import re
from bs4 import BeautifulSoup
from typing import Dict, List

# 匯入 Selenium 相關套件
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ⚙️ 設定區
DB_NAME = 'car_listings_v2.db'
LUXLIFE_LIST_URL = "https://luxlife.luxgen-motor.com.tw/car-list"


# ══════════════════════════════════════════════════════════════════════════════
#  🕷️ 步驟 1 & 2：Selenium 網頁抓取 (支援「加載更多」按鈕)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_luxlife_cars() -> List[Dict]:
    print("🚀 啟動 Selenium 隱形瀏覽器，準備抓取 LuxLife 原廠認證中古車...")
    standardized_cars = []

    # 隱形瀏覽器設定
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    driver = None
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.get(LUXLIFE_LIST_URL)

        print("⏳ 等待初始網頁動態渲染資料...")
        # 確保第一批車輛已經載入
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a.inner[href*='/car-page?id=']"))
        )

        # --- 不斷尋找並點擊「加載更多中古車」按鈕 ---
        click_count = 0
        while True:
            try:
                # 尋找包含「加載更多中古車」文字的 button (最多等 3 秒)
                load_more_btn = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(., '加載更多中古車')]"))
                )

                # 使用 JavaScript 強制點擊，避免被其他網頁元素遮擋
                driver.execute_script("arguments[0].click();", load_more_btn)
                click_count += 1
                print(f"👉 第 {click_count} 次點擊「加載更多中古車」...")

                time.sleep(2)  # 等待新車輛渲染
            except Exception:
                print("✅ 已經沒有按鈕，所有車輛載入完畢！")
                break

        print("🔍 開始解析全部資料...")

        # 將渲染完畢的 HTML 交給 BeautifulSoup
        html = driver.page_source
        soup = BeautifulSoup(html, 'html.parser')

        car_nodes = soup.find_all('a', class_='inner', href=re.compile(r'/car-page\?id='))

        if not car_nodes:
            print("⚠️ 找不到車輛節點，請確認網頁結構是否發生變化。")
            return []

        today_str = datetime.date.today().strftime("%Y-%m-%d")

        for node in car_nodes:
            href = node.get('href')
            car_id = re.search(r'id=(\d+)', href).group(1)
            full_url = f"https://luxlife.luxgen-motor.com.tw{href}"

            subtitle_div = node.find('div', class_='subtitle')
            title_div = node.find('div', class_='title')
            brand_text = subtitle_div.text.strip() if subtitle_div else "LUXGEN"
            model_text = title_div.text.strip() if title_div else "未知車型"

            desc_div = node.find('div', class_='desc')
            desc_divs = desc_div.find_all('div') if desc_div else []

            year, mileage, location = 0, 0, "未知"
            if len(desc_divs) >= 3:
                year = int(re.sub(r'\D', '', desc_divs[0].text.strip()) or 0)
                mileage = int(re.sub(r'\D', '', desc_divs[1].text.strip()) or 0)
                location = desc_divs[2].text.strip()

            price_div = node.find('div', class_='price')
            price_element = price_div.find('span') if price_div else None
            price = 0
            if price_element:
                price = int(re.sub(r'\D', '', price_element.text.strip()) or 0)

            price_type = "normal" if price > 0 else "inquiry"

            car_info = {
                "global_car_id": f"luxlife_{car_id}",
                "platform": "LUXLIFE",
                "brand": brand_text.upper(),
                "model": model_text.upper(),  # ✅ 這裡已加入強制轉大寫
                "year": year,
                "mileage": mileage,
                "location": location,
                "url": full_url,
                "status": "online",
                "last_seen": today_str,
                "price": price,
                "price_type": price_type
            }
            standardized_cars.append(car_info)

        print(f"🎉 成功獲取與清洗 {len(standardized_cars)} 筆 LuxLife 車輛資料！")
        return standardized_cars

    except Exception as e:
        print(f"❌ Selenium 爬蟲執行失敗: {e}")
        return []
    finally:
        if driver:
            driver.quit()


# ══════════════════════════════════════════════════════════════════════════════
#  🗄️ 步驟 3：資料庫比對與無縫寫入
# ══════════════════════════════════════════════════════════════════════════════
def save_luxlife_to_db(cars: List[Dict]):
    if not cars: return
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    try:
        conn = sqlite3.connect(DB_NAME, timeout=10)
        cursor = conn.cursor()

        fetched_ids = []
        new_cars_count, price_update_count = 0, 0

        for car in cars:
            gid = car["global_car_id"]
            fetched_ids.append(gid)

            cursor.execute("SELECT status FROM cars_master WHERE global_car_id = ?", (gid,))
            existing_car = cursor.fetchone()

            if existing_car:
                cursor.execute("""
                               UPDATE cars_master
                               SET last_seen = ?,
                                   status    = 'online',
                                   mileage   = ?,
                                   location  = ?
                               WHERE global_car_id = ?
                               """, (car["last_seen"], car["mileage"], car["location"], gid))
            else:
                cursor.execute("""
                               INSERT INTO cars_master
                               (global_car_id, platform, brand, model, year, mileage, location, url, status, first_seen,
                                last_seen)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                               """, (gid, car["platform"], car["brand"], car["model"],
                                     car["year"], car["mileage"], car["location"], car["url"],
                                     car["status"], today_str, car["last_seen"]))
                new_cars_count += 1

            cursor.execute("SELECT price FROM price_history WHERE global_car_id = ? ORDER BY record_date DESC LIMIT 1",
                           (gid,))
            last_price_record = cursor.fetchone()

            if not last_price_record or last_price_record[0] != car["price"]:
                cursor.execute("""
                               INSERT INTO price_history (global_car_id, price, price_type, record_date)
                               VALUES (?, ?, ?, ?)
                               """, (gid, car["price"], car["price_type"], today_str))
                if last_price_record: price_update_count += 1

        if fetched_ids:
            placeholders = ','.join(['?'] * len(fetched_ids))
            cursor.execute(f"""
                UPDATE cars_master 
                SET status = 'offline' 
                WHERE platform = 'LUXLIFE' AND status = 'online' AND global_car_id NOT IN ({placeholders})
            """, fetched_ids)
            offline_count = cursor.rowcount

        conn.commit()
        print(
            f"📊 LUXLIFE 資料庫更新完成: 新增 {new_cars_count} 台 | 價格異動 {price_update_count} 筆 | 偵測下架 {offline_count} 台")

    except Exception as e:
        conn.rollback()
        print(f"❌ 寫入資料庫時發生錯誤: {e}")
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
#  啟動區塊
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    luxlife_data = fetch_luxlife_cars()
    save_luxlife_to_db(luxlife_data)