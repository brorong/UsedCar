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
from selenium.common.exceptions import TimeoutException, WebDriverException

import db_manager  # 🔥 載入資料庫總管

# ⚙️ 設定區
LUXLIFE_LIST_URL = "https://luxlife.luxgen-motor.com.tw/car-list"


# ══════════════════════════════════════════════════════════════════════════════
#  🕷️ 步驟 1 & 2：Selenium 網頁抓取 (支援「加載更多」按鈕)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_luxlife_cars() -> List[Dict]:
    print("🚀 啟動 Selenium 隱形瀏覽器，準備抓取 LuxLife 原廠認證中古車...")
    standardized_cars = []

    # 隱形瀏覽器設定 (🔥 終極效能優化版)
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage") # 🛡️ 解決 GitHub 記憶體不足
    
    # 🏎️ 效能優化：不加載圖片，大幅加快速度並節省記憶體
    prefs = {"profile.managed_default_content_settings.images": 2}
    chrome_options.add_experimental_option("prefs", prefs)
    
    # ⏱️ Eager 模式 (DOM 載入完就開爬，不等廣告/圖片)
    chrome_options.page_load_strategy = 'eager'

    driver = None
    try:
        driver = webdriver.Chrome(options=chrome_options)
        
        # ⏳ 強制延長連線與腳本等待時間到 120 秒
        driver.set_page_load_timeout(120)
        driver.set_script_timeout(120)
        
        try:
            driver.get(LUXLIFE_LIST_URL)
        except TimeoutException:
            print("⚠️ 警告: 載入 LuxLife 頁面時超時，嘗試繼續執行...")
        except WebDriverException as e:
            print(f"❌ 網頁載入失敗: {e}")
            return []

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
                "car_id": car_id, # 🔥 加入純粹的 car_id 讓 db_manager 能靈活運用
                "global_car_id": f"LUXLIFE_{car_id}",
                "platform": "LUXLIFE",
                "brand": brand_text.upper(),
                "model": model_text.upper(),  # ✅ 強制轉大寫
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
#  啟動區塊：直接串接 db_manager 進行寫入與狀態更新
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    luxlife_data = fetch_luxlife_cars()
    if luxlife_data:
        # 🔥 直接將清洗好的資料交給總管，省去落落長的 SQL 寫入代碼
        db_manager.update_listings("LUXLIFE", luxlife_data)
