import re
import time
import random
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, NoSuchElementException
import db_manager  # 🔥 載入資料庫總管

BASE_URL = "https://www.sum.com.tw"
# 🔥 修正：拿掉無效的 p={page}，我們只用 URL 進入第一頁
SEARCH_URL = "https://www.sum.com.tw/carsearch.php?v=4&brand={brand}&model={model}"

# 🔥 v3 競品全線開戰名單 (SUM 專用)
SEARCH_TASKS = [
    # 本廠主力
    {"brand": "LUXGEN", "model": "n7", "display": "n7"},
    {"brand": "LUXGEN", "model": "URX", "display": "URX"},
    {"brand": "LUXGEN", "model": "U6", "display": "U6"},
    # n7 競品
    {"brand": "TOYOTA", "model": "bZ4X", "display": "bZ4X"},
    # U6 競品
    {"brand": "HONDA", "model": "CR-V", "display": "CR-V"},
    {"brand": "FORD", "model": "Kuga", "display": "Kuga"},
    # URX 競品
    {"brand": "NISSAN", "model": "X-Trail", "display": "X-Trail"},
    {"brand": "HYUNDAI", "model": "Santa Fe", "display": "Santa Fe"}
]


def clean_price(raw_str):
    if not raw_str or "電洽" in raw_str: return None, "inquiry"
    raw_str = raw_str.replace(",", "").replace(" ", "").replace("\n", "").replace("\t", "")
    m = re.search(r"([\d.]+)", raw_str)
    if m: return int(float(m.group(1)) * 10000), "normal"
    return None, "inquiry"


def clean_mileage(raw_str):
    if not raw_str: return 0
    raw_str = raw_str.replace(",", "").replace(" ", "").replace("\n", "").replace("公里", "").replace("km", "").replace("KM", "")
    m = re.search(r"(\d+)", raw_str)
    return int(m.group(1)) if m else 0


def parse_car_card(card_div, target_brand, target_model):
    try:
        raw_text = card_div.get_text(" ", strip=True)
        raw_text = re.sub(r"\s{2,}", " ", raw_text)

        # 臺轉台防呆
        raw_text = raw_text.replace("臺", "台")

        # 核心修正：將「空白」與「連字號」都去除，解決 CR-V 與 CRV 的差異
        clean_target = target_model.upper().replace(" ", "").replace("-", "")
        clean_raw = raw_text.upper().replace(" ", "").replace("-", "")

        # 嚴格過濾：標題或內文必須含有車型關鍵字
        if clean_target not in clean_raw:
            return None

        a_tag = card_div.find('a', href=re.compile(r'carinfo'))
        if not a_tag: return None
        href = a_tag.get("href", "")

        # 嘗試抓取車輛標題
        raw_title = a_tag.get_text(strip=True).replace("臺", "台")
        if not raw_title or len(raw_title) < 2:
            title_tag = card_div.find(class_=re.compile(r'subj|car-name'))
            raw_title = title_tag.get_text(strip=True).replace("臺", "台") if title_tag else raw_text[:30]

        # 強制轉大寫與統一名稱
        data = {
            "platform": "SUM",
            "car_id": "",
            "brand": target_brand.upper(),
            "model": target_model.upper(),
            "title": raw_title,
            "year": 0,
            "mileage": 0,
            "price": None,
            "price_type": "inquiry",
            "location": "未知",
            "url": ""
        }

        data["url"] = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"

        id_m = re.search(r"carinfo-(\d+)\.php", data["url"])
        if id_m:
            data["car_id"] = id_m.group(1)
        else:
            return None

        y_m = re.search(r"(\d{4})\s*年", raw_text)
        if y_m: data["year"] = int(y_m.group(1))

        m_m = re.search(r"([\d,]+)\s*(?:公里|km)", raw_text, re.IGNORECASE)
        if m_m: data["mileage"] = clean_mileage(m_m.group(1))

        loc_m = re.search(
            r"(台北市|新北市|桃園市|台中市|台南市|高雄市|新竹縣|新竹市|苗栗縣|彰化縣|南投縣|雲林縣|嘉義縣|嘉義市|屏東縣|宜蘭縣|花蓮縣|台東縣|基隆市|澎湖縣|金門縣|連江縣)",
            raw_text)
        if loc_m: data["location"] = loc_m.group(1)

        p_m = re.search(r"([\d.]+)\s*萬", raw_text)
        if p_m:
            data["price"], data["price_type"] = clean_price(p_m.group(0))
        elif "電洽" in raw_text:
            data["price"], data["price_type"] = None, "inquiry"

        return data
    except Exception:
        return None


def run_sum_scraper():
    print("[系統] 啟動 Selenium 隱形無頭模式，掃描 SUM 賞車網 (突破翻頁限制與防阻擋版)...")
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
    
    # 🛡️ 核心防護：解決 GitHub Actions 記憶體不足與逾時問題
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")
    
    # 🛡️ 新增：防封鎖特徵隱藏與真實 User-Agent
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    
    # 🏎️ 效能優化：不加載圖片，大幅加快網頁解析速度
    prefs = {"profile.managed_default_content_settings.images": 2}
    chrome_options.add_experimental_option("prefs", prefs)
    
    # ⏱️ 載入策略修改：改回 normal (等待腳本載入)，避免 eager 導致 JS 渲染不及而抓不到資料
    chrome_options.page_load_strategy = 'normal'

    driver = webdriver.Chrome(options=chrome_options)
    
    # 🛡️ 新增：抹除 WebDriver 屬性，防阻擋
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    # ⏳ 強制延長等待時間
    driver.set_page_load_timeout(120)
    driver.set_script_timeout(120)

    valid_cars = []
    global_seen_ids = set()

    try:
        for task in SEARCH_TASKS:
            # 🛡️ 新增：每次搜尋前隨機休息，模擬人類節奏，避免觸發 WAF
            time.sleep(random.uniform(3, 7))
            
            page_count = 1
            model_total = 0
            model_display = task['display']
            print(f"  🔍 開始搜尋 {model_display}...")

            url = SEARCH_URL.format(brand=task['brand'], model=task['model'])
            
            # 🛡️ 新增：重試機制，針對單次網路錯誤進行補救
            load_success = False
            for attempt in range(3):
                try:
                    driver.get(url)
                    load_success = True
                    break
                except TimeoutException:
                    print(f"  ⚠️ {model_display} 網頁載入超時 (第 {attempt+1} 次嘗試)，休息後重試...")
                    time.sleep(5)
                except WebDriverException as e:
                    print(f"  ⚠️ {model_display} 網頁載入失敗 (第 {attempt+1} 次嘗試): {e}")
                    time.sleep(5)
            
            # 若重試三次皆失敗，則跳過此車款
            if not load_success:
                print(f"  ❌ {model_display} 三次連線嘗試皆失敗，跳過。")
                continue

            while True:
                try:
                    WebDriverWait(driver, 10).until(EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "div.shop-list, a.subj, li:has(a[href*='carinfo'])")))
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
                    time.sleep(2)
                except TimeoutException:
                    break

                soup = BeautifulSoup(driver.page_source, "html.parser")
                cards = soup.select('div.shop-list, li:has(a[href*="carinfo"])')

                new_count = 0
                for card in cards:
                    if not card.find('a', href=re.compile(r'carinfo')): continue

                    car_data = parse_car_card(card, task['brand'], model_display)
                    if car_data and car_data["car_id"] not in global_seen_ids:
                        global_seen_ids.add(car_data["car_id"])
                        valid_cars.append(car_data)
                        new_count += 1
                        model_total += 1

                # 防死循環與結束判斷
                if new_count == 0 or page_count >= 50:
                    break

                # 🔥 模擬人類點擊下一頁按鈕
                next_page_target = str(page_count + 1)
                try:
                    # 嘗試點擊精確的數字頁碼
                    next_btn = driver.find_element(By.XPATH,
                                                   f"//a[text()='{next_page_target}'] | //li/a[text()='{next_page_target}']")
                    driver.execute_script("arguments[0].click();", next_btn)
                    page_count += 1
                    time.sleep(3)  # 等待 AJAX/畫面刷新
                except NoSuchElementException:
                    try:
                        # 備案：點擊下一頁或 > 按鈕
                        next_btn = driver.find_element(By.XPATH,
                                                       "//a[contains(@class, 'next') or contains(text(), '下一頁') or contains(text(), '下頁') or text()='>']")
                        driver.execute_script("arguments[0].click();", next_btn)
                        page_count += 1
                        time.sleep(3
