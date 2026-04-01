import re
import time
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, NoSuchElementException
import db_manager  # 🔥 載入資料庫總管

BASE_URL = "https://www.isave.com.tw"
# 🔥 修正：捨棄無效的 p 參數，我們改用真實點擊翻頁
SEARCH_URL = "https://www.isave.com.tw/cars.aspx?brand={brand}&style={style}"

# 🔥 v3 競品全線開戰名單 (SAVE 專用)
SEARCH_TASKS = [
    {"brand": "LUXGEN", "style": "n7", "display": "n7"},
    {"brand": "LUXGEN", "style": "URX", "display": "URX"},
    {"brand": "LUXGEN", "style": "U6", "display": "U6"},
    {"brand": "TOYOTA", "style": "BZ4X", "display": "BZ4X"},
    {"brand": "HONDA", "style": "CR-V", "display": "CR-V"},
    {"brand": "FORD", "style": "KUGA", "display": "KUGA"},
    {"brand": "NISSAN", "style": "X-TRAIL", "display": "X-TRAIL"},
    {"brand": "HYUNDAI", "style": "SANTA+FE", "display": "Santa Fe"}
]


def clean_price(raw_str):
    if not raw_str or "電洽" in raw_str: return None, "inquiry"
    raw_str = raw_str.replace(",", "").replace(" ", "").replace("\n", "").replace("\t", "")
    m = re.search(r"([\d.]+)", raw_str)
    if m: return int(float(m.group(1)) * 10000), "normal"
    return None, "inquiry"


def clean_mileage(raw_str):
    if not raw_str: return 0
    raw_str = raw_str.replace(",", "").replace(" ", "").replace("\n", "").replace("公里", "").replace("km", "").replace(
        "KM", "")
    m = re.search(r"(\d+)", raw_str)
    return int(m.group(1)) if m else 0


def parse_car_card(card_div, target_brand, target_model):
    raw_text = card_div.get_text(" ", strip=True)
    raw_text = re.sub(r"\s{2,}", " ", raw_text).replace("臺", "台")

    if target_model.upper().replace(" ", "") not in raw_text.upper().replace(" ", ""):
        return None

    a_tag = card_div.find('a', href=re.compile(r'(car_detail\.aspx|car\.aspx\?cid=)', re.IGNORECASE))
    if not a_tag: return None

    raw_title = a_tag.get_text(strip=True).replace("臺", "台")
    if not raw_title or len(raw_title) < 2:
        title_elem = card_div.find(class_=re.compile(r'title|car-name|name', re.IGNORECASE))
        raw_title = title_elem.get_text(strip=True).replace("臺", "台") if title_elem else raw_text[:30]

    data = {
        "platform": "SAVE",
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

    href = a_tag.get("href", "")
    data["url"] = href if href.startswith("http") else BASE_URL + "/" + href.lstrip('/')
    id_m = re.search(r"[ci]d=(\d+)", data["url"], re.IGNORECASE)
    if not id_m: return None
    data["car_id"] = id_m.group(1)

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


def run_save_scraper():
    print("[系統] 啟動 Selenium 隱形模式，掃描 SAVE 認證車聯盟 (突破 PostBack 限制版)...")
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument('--ignore-certificate-errors')
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
    
    # 🛡️ 核心防護：解決 GitHub Actions 記憶體不足與 Linux 權限問題
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")
    
    # 🏎️ 效能優化：不加載圖片，大幅加快網頁解析速度
    prefs = {"profile.managed_default_content_settings.images": 2}
    chrome_options.add_experimental_option("prefs", prefs)
    
    # ⏱️ 載入策略：DOM 樹出來就開爬，不等廣告或過度肥大的元素
    chrome_options.page_load_strategy = 'eager'

    driver = webdriver.Chrome(options=chrome_options)
    
    # ⏳ 強制延長等待時間至 120 秒，避免 GitHub 網路波動導致的 Timeout
    driver.set_page_load_timeout(120)
    driver.set_script_timeout(120)

    valid_cars = []
    global_seen_ids = set()

    try:
        for task in SEARCH_TASKS:
            page_count = 1
            model_total = 0
            model_display = task['display']
            print(f"  🔍 開始搜尋 {model_display}...")

            # 🔥 修正：只在第一頁使用 get() 載入，後續全靠 Selenium 點擊翻頁
            url = SEARCH_URL.format(brand=task['brand'], style=task['style'])
            try:
                driver.get(url)
            except TimeoutException:
                print(f"  ⚠️ {model_display} 網頁載入超時，嘗試繼續執行...")
            except WebDriverException as e:
                print(f"  ❌ {model_display} 網頁載入失敗: {e}，跳過。")
                continue

            while True:
                try:
                    WebDriverWait(driver, 10).until(EC.presence_of_element_located(
                        (By.CSS_SELECTOR, ".car-block-type2_item, .car_item, .car-card")))

                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
                    time.sleep(2)
                except TimeoutException:
                    break  # 網頁卡死或真的沒資料了

                soup = BeautifulSoup(driver.page_source, "html.parser")
                cards = soup.select('.car-block-type2_item, div[class*="car_item"], .car-card')

                new_count = 0
                for card in cards:
                    car_data = parse_car_card(card, task['brand'], model_display)
                    if car_data and car_data["car_id"] not in global_seen_ids:
                        global_seen_ids.add(car_data["car_id"])
                        valid_cars.append(car_data)
                        new_count += 1
                        model_total += 1

                if new_count == 0 or page_count >= 50:
                    break  # 沒有新車或到達極限

                # 🔥 核心修正：模擬人類點擊網頁底下的分頁按鈕
                next_page_target = str(page_count + 1)
                try:
                    # 尋找直接包含數字的 <a> 標籤 (ASP.NET 最常見做法)
                    next_btn = driver.find_element(By.XPATH,
                                                   f"//a[text()='{next_page_target}'] | //a[contains(@href, 'Page${next_page_target}')]")
                    driver.execute_script("arguments[0].click();", next_btn)
                    page_count += 1
                    time.sleep(3)  # 給 ASP.NET 伺服器處理刷新 POST 請求的時間
                except NoSuchElementException:
                    try:
                        # 備案：尋找「下一頁」或「>」的箭頭符號
                        next_btn = driver.find_element(By.XPATH,
                                                       "//a[contains(text(), '下一頁') or contains(text(), '下頁') or text()='>']")
                        driver.execute_script("arguments[0].click();", next_btn)
                        page_count += 1
                        time.sleep(3)
                    except NoSuchElementException:
                        break  # 真的連按鈕都找不到了，代表翻到最後一頁

            print(f"  ✅ {model_display} 完成！共抓取 {model_total} 筆資料 (掃描至第 {page_count} 頁)")

    finally:
        driver.quit()

    if valid_cars:
        print(f"[系統] SAVE 認證車聯盟掃描完畢，總計入庫 {len(valid_cars)} 筆。")
        db_manager.update_listings("SAVE", valid_cars)


if __name__ == "__main__":
    run_save_scraper()
