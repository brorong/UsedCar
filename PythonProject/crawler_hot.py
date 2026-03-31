import re
import time
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import db_manager  # 🔥 載入資料庫總管

BASE_URL = "https://www.hotcar.com.tw"
SEARCH_URL = "https://www.hotcar.com.tw/UsedCarSell/CarFilter?vKeyword={keyword}"

TARGET_CARS = {
    "Luxgen": ["n7", "URX", "U6"],
    "Toyota": ["bZ4X"],
    "Honda": ["CR-V"],
    "Ford": ["Kuga"],
    "Nissan": ["X-Trail"],
    "Hyundai": ["Santa Fe"]
}


# ── 數值清洗工具 ──────────────────────────────────────────────────────────
def clean_price(raw_str):
    if not raw_str or "電洽" in raw_str: return None, "inquiry"
    raw_str = raw_str.replace(",", "").replace(" ", "").replace("\n", "").replace("\t", "")
    m = re.search(r"([\d.]+)", raw_str)
    if m:
        val = float(m.group(1))
        # 修正：HOT 價格通常是「萬」，val < 5000 判斷為萬元單位
        return int(val * 10000) if "萬" in raw_str or val < 5000 else int(val), "normal"
    return None, "inquiry"


def clean_mileage(raw_str):
    if not raw_str: return 0
    raw_str = raw_str.replace(",", "").replace(" ", "").replace("\n", "").replace("公里", "")
    m = re.search(r"(\d+)", raw_str)
    return int(m.group(1)) if m else 0


def parse_car_card(data_box, brand, model):
    title_tag = data_box.find('a', class_='title')
    if not title_tag: return None

    raw_title = title_tag.get_text(strip=True)
    # 嚴格過濾：標題必須含有車型關鍵字
    if model.upper() not in raw_title.upper():
        return None

    # 🔥 核心修正：將 platform、brand 與 model 強制轉為大寫
    data = {
        "platform": "HOT",
        "car_id": "",
        "brand": brand.upper(),
        "model": model.upper(),
        "title": raw_title,
        "year": 0,
        "mileage": 0,
        "price": None,
        "price_type": "inquiry",
        "location": "未知",
        "url": ""
    }

    href = title_tag.get('href', '')
    data["url"] = BASE_URL + href if href.startswith('/') else href
    id_m = re.search(r"vSeqNo=(\d+)", data["url"])
    data["car_id"] = id_m.group(1) if id_m else ""
    if not data["car_id"]: return None

    sec_info = data_box.find('p', class_='secInfo')
    if sec_info:
        info_text = sec_info.get_text("|", strip=True)
        y_m = re.search(r"(\d{4})", info_text)
        if y_m: data["year"] = int(y_m.group(1))
        m_m = re.search(r"([\d,]+)\s*公里", info_text)
        if m_m: data["mileage"] = clean_mileage(m_m.group(1))
        loc_tag = sec_info.find('b', class_='mark_county')
        if loc_tag: data["location"] = loc_tag.text.strip()

    price_tag = data_box.find('div', class_='price')
    if price_tag:
        price_val = price_tag.find('b')
        if price_val: data["price"], data["price_type"] = clean_price(price_val.text + "萬")
    return data


# ── 主爬蟲邏輯 (終極多重翻頁版) ───────────────────────────────────────────
def run_hot_scraper():
    print("[系統] 啟動 Selenium 深度掃描模式 (多重翻頁特徵捕捉)...")
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])

    driver = webdriver.Chrome(options=chrome_options)
    valid_cars = []
    global_seen_ids = set()

    try:
        for brand, models in TARGET_CARS.items():
            for model_name in models:
                url = SEARCH_URL.format(keyword=model_name)
                driver.get(url)

                page_count = 1
                model_total = 0

                print(f"  🔍 開始搜尋 {model_name}...")

                while True:
                    try:
                        # 1. 等待車輛卡片載入
                        WebDriverWait(driver, 10).until(
                            EC.presence_of_element_located((By.CLASS_NAME, "dataBox"))
                        )
                        # 捲動網頁確保圖片與底部分頁列被載入
                        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        time.sleep(2)

                        # 2. 解析當前頁面
                        soup = BeautifulSoup(driver.page_source, "html.parser")
                        boxes = soup.find_all('div', class_='dataBox')

                        new_on_page = 0
                        for box in boxes:
                            car_data = parse_car_card(box, brand, model_name)
                            if car_data and car_data["car_id"] not in global_seen_ids:
                                global_seen_ids.add(car_data["car_id"])
                                valid_cars.append(car_data)
                                new_on_page += 1
                                model_total += 1

                        # 3. 嘗試翻頁 (雙重防護策略)
                        next_page_target = page_count + 1
                        try:
                            # 策略 A：直接尋找確切的「下一頁數字」按鈕 (例如 <a>2</a>)
                            next_btn = driver.find_element(By.XPATH,
                                                           f"//a[text()='{next_page_target}'] | //li/a[text()='{next_page_target}']")
                            driver.execute_script("arguments[0].click();", next_btn)
                            page_count += 1
                            time.sleep(3)  # 給予 AJAX 載入時間

                        except NoSuchElementException:
                            try:
                                # 策略 B：如果找不到數字，嘗試各種「下一頁」可能的形式
                                next_btn = driver.find_element(By.XPATH,
                                                               "//a[contains(@class, 'next') or contains(text(), '下一頁') or contains(text(), '下頁') or contains(text(), '>') or @aria-label='Next']")

                                # 檢查是否為 disabled 狀態 (到達最後一頁)
                                parent_class = next_btn.find_element(By.XPATH, "./..").get_attribute("class") or ""
                                if "disabled" in parent_class:
                                    break

                                driver.execute_script("arguments[0].click();", next_btn)
                                page_count += 1
                                time.sleep(3)

                            except NoSuchElementException:
                                # 策略 A 跟 B 都失敗，代表真的沒有下一頁了
                                break

                    except TimeoutException:
                        # 該頁面 10 秒內都無法生出 .dataBox，代表已無資料
                        break

                print(f"  ✅ {model_name} 完成！共抓取 {model_total} 筆資料 (掃描至第 {page_count} 頁)")

    finally:
        driver.quit()

    if valid_cars:
        print(f"[系統] HOT 大聯盟掃描完畢，總計入庫 {len(valid_cars)} 筆。")
        # 🔥 將傳給 db_manager 的平台標識也改為大寫
        db_manager.update_listings("HOT", valid_cars)


if __name__ == "__main__":
    run_hot_scraper()
