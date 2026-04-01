import re
import time
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, WebDriverException, NoSuchElementException
)
import db_manager  # 🔥 載入資料庫總管

BASE_URL = "https://www.sum.com.tw"
SEARCH_URL = "https://www.sum.com.tw/carsearch.php?v=4&brand={brand}&model={model}"

SEARCH_TASKS = [
    {"brand": "LUXGEN",  "model": "n7",        "display": "n7"},
    {"brand": "LUXGEN",  "model": "URX",       "display": "URX"},
    {"brand": "LUXGEN",  "model": "U6",        "display": "U6"},
    {"brand": "TOYOTA",  "model": "bZ4X",      "display": "bZ4X"},
    {"brand": "HONDA",   "model": "CR-V",      "display": "CR-V"},
    {"brand": "FORD",    "model": "Kuga",      "display": "Kuga"},
    {"brand": "NISSAN",  "model": "X-Trail",   "display": "X-Trail"},
    {"brand": "HYUNDAI", "model": "Santa Fe",  "display": "Santa Fe"},
]


# ──────────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────────

def clean_price(raw_str):
    if not raw_str or "電洽" in raw_str:
        return None, "inquiry"
    raw_str = re.sub(r"[\s,\n\t]", "", raw_str)
    m = re.search(r"([\d.]+)", raw_str)
    if m:
        return int(float(m.group(1)) * 10_000), "normal"
    return None, "inquiry"


def clean_mileage(raw_str):
    if not raw_str:
        return 0
    raw_str = re.sub(r"[,\s公里kmKM]", "", raw_str)
    m = re.search(r"(\d+)", raw_str)
    return int(m.group(1)) if m else 0


def parse_car_card(card_div, target_brand, target_model):
    try:
        raw_text = card_div.get_text(" ", strip=True)
        raw_text = re.sub(r"\s{2,}", " ", raw_text).replace("臺", "台")

        clean_target = target_model.upper().replace(" ", "").replace("-", "")
        clean_raw   = raw_text.upper().replace(" ", "").replace("-", "")
        if clean_target not in clean_raw:
            return None

        a_tag = card_div.find("a", href=re.compile(r"carinfo"))
        if not a_tag:
            return None

        href      = a_tag.get("href", "")
        raw_title = a_tag.get_text(strip=True).replace("臺", "台")
        if not raw_title or len(raw_title) < 2:
            title_tag = card_div.find(class_=re.compile(r"subj|car-name"))
            raw_title = (
                title_tag.get_text(strip=True).replace("臺", "台")
                if title_tag else raw_text[:30]
            )

        data = {
            "platform":   "SUM",
            "car_id":     "",
            "brand":      target_brand.upper(),
            "model":      target_model.upper(),
            "title":      raw_title,
            "year":       0,
            "mileage":    0,
            "price":      None,
            "price_type": "inquiry",
            "location":   "未知",
            "url":        "",
        }

        data["url"] = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"
        id_m = re.search(r"carinfo-(\d+)\.php", data["url"])
        if not id_m:
            return None
        data["car_id"] = id_m.group(1)

        y_m = re.search(r"(\d{4})\s*年", raw_text)
        if y_m:
            data["year"] = int(y_m.group(1))

        m_m = re.search(r"([\d,]+)\s*(?:公里|km)", raw_text, re.IGNORECASE)
        if m_m:
            data["mileage"] = clean_mileage(m_m.group(1))

        loc_m = re.search(
            r"(台北市|新北市|桃園市|台中市|台南市|高雄市|新竹縣|新竹市|苗栗縣|"
            r"彰化縣|南投縣|雲林縣|嘉義縣|嘉義市|屏東縣|宜蘭縣|花蓮縣|台東縣|"
            r"基隆市|澎湖縣|金門縣|連江縣)",
            raw_text,
        )
        if loc_m:
            data["location"] = loc_m.group(1)

        p_m = re.search(r"([\d.]+)\s*萬", raw_text)
        if p_m:
            data["price"], data["price_type"] = clean_price(p_m.group(0))
        elif "電洽" in raw_text:
            data["price"], data["price_type"] = None, "inquiry"

        return data

    except Exception:
        return None


# ──────────────────────────────────────────────
# Driver 建構（相容 GitHub Actions）
# ──────────────────────────────────────────────

def build_driver() -> webdriver.Chrome:
    options = Options()

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    
    # 🔥 加回：忽略可能導致連線中斷的憑證錯誤
    options.add_argument("--ignore-certificate-errors")

    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)

    options.add_experimental_option(
        "prefs", {"profile.managed_default_content_settings.images": 2}
    )
    
    # 🔥 終極解法：將載入策略改為 none，不理會拖台錢的廣告與無窮迴圈 JS
    options.page_load_strategy = "none"

    driver = webdriver.Chrome(options=options)

    # ⏳ 延長超時容忍度至 60 秒
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(60)

    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )

    return driver


# ──────────────────────────────────────────────
# 主爬蟲邏輯
# ──────────────────────────────────────────────

def _wait_for_list(driver, timeout=20):  # 🔥 放寬元素等待時間至 20 秒
    """等待車輛列表出現，回傳 True/False。"""
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div.shop-list, a.subj, li a[href*='carinfo']")
            )
        )
        return True
    except TimeoutException:
        return False


def _get_first_href(driver) -> str:
    """取得當前頁面第一台車的 href，用於判斷是否真的換頁。"""
    try:
        return driver.find_element(
            By.CSS_SELECTOR, "li a[href*='carinfo'], div.shop-list a[href*='carinfo']"
        ).get_attribute("href")
    except Exception:
        return ""


def _click_next_page(driver, next_page_num: int) -> bool:
    """
    嘗試點擊下一頁，成功回傳 True，找不到按鈕回傳 False。
    """
    xpaths = [
        f"//a[normalize-space(text())='{next_page_num}']",
        f"//li/a[normalize-space(text())='{next_page_num}']",
        "//a[contains(@class,'next')]",
        "//a[contains(text(),'下一頁')]",
        "//a[contains(text(),'下頁')]",
        "//a[normalize-space(text())='>']",
    ]
    for xpath in xpaths:
        try:
            btn = driver.find_element(By.XPATH, xpath)
            driver.execute_script("arguments[0].scrollIntoView(true);", btn)
            driver.execute_script("arguments[0].click();", btn)
            return True
        except NoSuchElementException:
            continue
    return False


def _scrape_task(driver, task: dict, global_seen_ids: set) -> list:
    """爬取單一車款，回傳該車款所有新資料。"""
    model_display = task["display"]
    url           = SEARCH_URL.format(brand=task["brand"], model=task["model"])
    results       = []
    page_count    = 1

    print(f"  🔍 開始搜尋 {model_display}...")

    try:
        driver.get(url)
    except TimeoutException:
        print(f"    ⚠️ {model_display} 初始頁逾時，嘗試強制介入擷取...")
    except WebDriverException as exc:
        print(f"    ❌ {model_display} 載入失敗（{exc.__class__.__name__}），跳過。")
        return results

    while True:
        if not _wait_for_list(driver):
            print(f"    ⚠️ {model_display} 第 {page_count} 頁找不到車輛列表 (可能是無資料或遭阻擋)。")
            break

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
        time.sleep(1)

        old_first_href = _get_first_href(driver)

        soup  = BeautifulSoup(driver.page_source, "html.parser")
        cards = soup.select('div.shop-list, li:has(a[href*="carinfo"])')

        new_count = 0
        for card in cards:
            if not card.find("a", href=re.compile(r"carinfo")):
                continue
            car_data = parse_car_card(card, task["brand"], model_display)
            if car_data and car_data["car_id"] not in global_seen_ids:
                global_seen_ids.add(car_data["car_id"])
                results.append(car_data)
                new_count += 1

        print(f"    第 {page_count} 頁：擷取 {new_count} 筆")

        if new_count == 0 or page_count >= 50:
            break

        if not _click_next_page(driver, page_count + 1):
            print(f"    -> 已達最後一頁，停止翻頁。")
            break

        time.sleep(3)

        new_first_href = _get_first_href(driver)
        if new_first_href and new_first_href == old_first_href:
            print("    ⚠️ 頁面疑似未刷新，額外等待 2 秒...")
            time.sleep(2)

        page_count += 1

    print(f"  ✅ {model_display} 完成！共抓取 {len(results)} 筆資料")
    return results


def run_sum_scraper():
    print("[系統] 啟動 Selenium，掃描 SUM 賞車網...")
    driver     = build_driver()
    valid_cars = []
    seen_ids   = set()

    try:
        for task in SEARCH_TASKS:
            cars = _scrape_task(driver, task, seen_ids)
            valid_cars.extend(cars)
    finally:
        driver.quit()

    if valid_cars:
        print(f"[系統] SUM 賞車網掃描完畢，總計入庫 {len(valid_cars)} 筆。")
        db_manager.update_listings("SUM", valid_cars)
    else:
        print("[系統] 未抓取到任何資料，請檢查網站結構或網路連線。")


if __name__ == "__main__":
    run_sum_scraper()
