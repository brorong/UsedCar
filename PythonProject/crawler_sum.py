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

BASE_URL = "https://www.sum.com.tw"
SEARCH_URL = "https://www.sum.com.tw/carsearch.php?v=4&brand={brand}&model={model}"

SEARCH_TASKS = [
    {"brand": "LUXGEN", "model": "n7", "display": "n7"},
    {"brand": "LUXGEN", "model": "URX", "display": "URX"},
    {"brand": "LUXGEN", "model": "U6", "display": "U6"},
    {"brand": "TOYOTA", "model": "bZ4X", "display": "bZ4X"},
    {"brand": "HONDA", "model": "CR-V", "display": "CR-V"},
    {"brand": "FORD", "model": "Kuga", "display": "Kuga"},
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
    raw_str = raw_str.replace(",", "").replace(" ", "").replace("\n", "").replace("公里", "").replace("km", "").replace(
        "KM", "")
    m = re.search(r"(\d+)", raw_str)
    return int(m.group(1)) if m else 0


def parse_car_card(card_div, target_brand, target_model):
    try:
        raw_text = card_div.get_text(" ", strip=True)
        raw_text = re.sub(r"\s{2,}", " ", raw_text).replace("臺", "台")

        clean_target = target_model.upper().replace(" ", "").replace("-", "")
        clean_raw = raw_text.upper().replace(" ", "").replace("-", "")

        if clean_target not in clean_raw:
            return None

        a_tag = card_div.find('a', href=re.compile(r'carinfo'))
        if not a_tag: return None
        href = a_tag.get("href", "")

        raw_title = a_tag.get_text(strip=True).replace("臺", "台")
        if not raw_title or len(raw_title) < 2:
            title_tag = card_div.find(class_=re.compile(r'subj|car-name'))
            raw_title = title_tag.get_text(strip=True).replace("臺", "台") if title_tag else raw_text[:30]

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
    print("[系統] 啟動 Selenium 隱形模式，掃描 SUM 賞車網 (高規防護版)...")
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])

    # 🛡️ 核心防護：加入 User-Agent 偽裝與解除記憶體限制
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")

    # 🏎️ 效能優化：不加載圖片
    prefs = {"profile.managed_default_content_settings.images": 2}
    chrome_options.add_experimental_option("prefs", prefs)
    chrome_options.page_load_strategy = 'eager'

    driver = webdriver.Chrome(options=chrome_options)

    # ⏳ 停損點設為 30 秒，超過就強制判定超時，絕不卡死
    driver.set_page_load_timeout(30)
    driver.set_script_timeout(30)

    valid_cars = []
    global_seen_ids = set()

    try:
        for task in SEARCH_TASKS:
            page_count = 1
            model_total = 0
            model_display = task['display']
            print(f"  🔍 開始搜尋 {model_display}...")

            url = SEARCH_URL.format(brand=task['brand'], model=task['model'])
            try:
                driver.get(url)
            except TimeoutException:
                print(f"    ⚠️ {model_display} 初始網頁載入超過 30 秒，嘗試強制繼續解析...")
            except WebDriverException as e:
                print(f"    ❌ {model_display} 網頁載入失敗，跳過。")
                continue

            while True:
                try:
                    WebDriverWait(driver, 10).until(EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "div.shop-list, a.subj, li:has(a[href*='carinfo'])")))
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
                    time.sleep(1)
                except TimeoutException:
                    print(f"    ⚠️ 找不到車輛列表，該車型可能無資料或網頁卡死。")
                    break

                # 記錄翻頁前的首台車輛
                old_first_car = ""
                try:
                    old_first_car = driver.find_element(By.CSS_SELECTOR,
                                                        "li:has(a[href*='carinfo']) a, div.shop-list a").get_attribute(
                        "href")
                except:
                    pass

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

                if new_count == 0 or page_count >= 50:
                    break

                next_page_target = str(page_count + 1)
                try:
                    # 尋找精準的下一頁按鈕
                    next_btn = driver.find_element(By.XPATH,
                                                   f"//a[text()='{next_page_target}'] | //li/a[text()='{next_page_target}']")
                    print(f"    -> 找到第 {next_page_target} 頁，執行翻頁...")
                    driver.execute_script("arguments[0].click();", next_btn)

                    time.sleep(3)  # 給 SUM 伺服器反應時間

                    # 檢查第一台車是否改變了，確保真的有換頁
                    try:
                        current_first_car = driver.find_element(By.CSS_SELECTOR,
                                                                "li:has(a[href*='carinfo']) a, div.shop-list a").get_attribute(
                            "href")
                        if current_first_car == old_first_car:
                            print("    ⚠️ 網頁疑似未刷新，給予額外 2 秒緩衝...")
                            time.sleep(2)
                    except:
                        pass

                    page_count += 1

                except NoSuchElementException:
                    try:
                        # 備案按鈕
                        next_btn = driver.find_element(By.XPATH,
                                                       "//a[contains(@class, 'next') or contains(text(), '下一頁') or contains(text(), '下頁') or text()='>']")
                        print(f"    -> 點擊下一頁按鈕...")
                        driver.execute_script("arguments[0].click();", next_btn)
                        time.sleep(3)
                        page_count += 1
                    except NoSuchElementException:
                        print("    -> 找不到下一頁按鈕，已達最後一頁。")
                        break

            print(f"  ✅ {model_display} 完成！共抓取 {model_total} 筆資料")

    finally:
        driver.quit()

    if valid_cars:
        print(f"[系統] SUM 賞車網掃描完畢，總計入庫 {len(valid_cars)} 筆。")
        db_manager.update_listings("SUM", valid_cars)


if __name__ == "__main__":
    run_sum_scraper()
