"""
crawler_sum.py  ——  SUM 賞車網爬蟲（抗封鎖強化版）

策略說明
────────
1. 優先用 requests + BeautifulSoup（速度快、不觸發 bot 偵測）
2. 若靜態請求失敗（JS 渲染頁或被擋），自動 fallback 到 undetected-chromedriver (或標準 Selenium)
3. 針對 SUM 防火牆特性，將 page_load_strategy 設為 'none' 強制突圍
4. 加入 retry + exponential backoff，應對偶發性封鎖
"""

import re
import time
import random
import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, NoSuchElementException

try:
    import undetected_chromedriver as uc
    UC_AVAILABLE = True
except ImportError:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    UC_AVAILABLE = False
    logging.warning("[警告] undetected-chromedriver 未安裝，改用標準 Selenium（較易被封鎖）")

import db_manager

# ──────────────────────────────────────────────
# 常數
# ──────────────────────────────────────────────

BASE_URL   = "https://www.sum.com.tw"
SEARCH_URL = "https://www.sum.com.tw/carsearch.php?v=4&brand={brand}&model={model}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE_URL,
}

SEARCH_TASKS = [
    {"brand": "LUXGEN",  "model": "n7",       "display": "n7"},
    {"brand": "LUXGEN",  "model": "URX",      "display": "URX"},
    {"brand": "LUXGEN",  "model": "U6",       "display": "U6"},
    {"brand": "TOYOTA",  "model": "bZ4X",     "display": "bZ4X"},
    {"brand": "HONDA",   "model": "CR-V",     "display": "CR-V"},
    {"brand": "FORD",    "model": "Kuga",     "display": "Kuga"},
    {"brand": "NISSAN",  "model": "X-Trail",  "display": "X-Trail"},
    {"brand": "HYUNDAI", "model": "Santa Fe", "display": "Santa Fe"},
]

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────────

def clean_price(raw_str):
    if not raw_str or "電洽" in raw_str:
        return None, "inquiry"
    raw_str = re.sub(r"[\s,\n\t]", "", raw_str)
    m = re.search(r"([\d.]+)", raw_str)
    return (int(float(m.group(1)) * 10_000), "normal") if m else (None, "inquiry")


def clean_mileage(raw_str):
    if not raw_str:
        return 0
    raw_str = re.sub(r"[,\s公里kmKM]", "", raw_str)
    m = re.search(r"(\d+)", raw_str)
    return int(m.group(1)) if m else 0


def parse_car_card(card_div, target_brand: str, target_model: str) -> Optional[dict]:
    try:
        raw_text = card_div.get_text(" ", strip=True)
        raw_text = re.sub(r"\s{2,}", " ", raw_text).replace("臺", "台")

        clean_target = target_model.upper().replace(" ", "").replace("-", "")
        if clean_target not in raw_text.upper().replace(" ", "").replace("-", ""):
            return None

        a_tag = card_div.find("a", href=re.compile(r"carinfo"))
        if not a_tag:
            return None

        href      = a_tag.get("href", "")
        raw_title = a_tag.get_text(strip=True).replace("臺", "台") or raw_text[:30]
        url       = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"
        id_m      = re.search(r"carinfo-(\d+)\.php", url)
        if not id_m:
            return None

        data = {
            "platform":   "SUM",
            "car_id":     id_m.group(1),
            "brand":      target_brand.upper(),
            "model":      target_model.upper(),
            "title":      raw_title,
            "year":       0,
            "mileage":    0,
            "price":      None,
            "price_type": "inquiry",
            "location":   "未知",
            "url":        url,
        }

        if y := re.search(r"(\d{4})\s*年", raw_text):
            data["year"] = int(y.group(1))
        if km := re.search(r"([\d,]+)\s*(?:公里|km)", raw_text, re.I):
            data["mileage"] = clean_mileage(km.group(1))
        if loc := re.search(
            r"(台北市|新北市|桃園市|台中市|台南市|高雄市|新竹[縣市]|苗栗縣|彰化縣|"
            r"南投縣|雲林縣|嘉義[縣市]|屏東縣|宜蘭縣|花蓮縣|台東縣|基隆市|澎湖縣|金門縣|連江縣)",
            raw_text,
        ):
            data["location"] = loc.group(1)
        if pm := re.search(r"([\d.]+)\s*萬", raw_text):
            data["price"], data["price_type"] = clean_price(pm.group(0))
        elif "電洽" in raw_text:
            data["price_type"] = "inquiry"

        return data
    except Exception:
        return None


# ──────────────────────────────────────────────
# 策略 1：純 requests（速度最快、最不易被偵測）
# ──────────────────────────────────────────────

def _fetch_with_requests(url: str, retries: int = 3) -> Optional[BeautifulSoup]:
    session = requests.Session()
    session.headers.update(HEADERS)

    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 200:
                # 🔥 已修改為 html.parser
                soup = BeautifulSoup(resp.text, "html.parser")
                if soup.select('div.shop-list, li a[href*="carinfo"]'):
                    return soup
                log.debug("      [requests] 頁面無車輛資料（可能需要 JS 渲染或遭阻擋）")
                return None
            elif resp.status_code in (403, 429):
                wait = 2 ** attempt + random.uniform(0, 1)
                log.warning(f"      [requests] HTTP {resp.status_code}，等待 {wait:.1f}s 重試...")
                time.sleep(wait)
            else:
                log.warning(f"      [requests] HTTP {resp.status_code}")
                return None
        except requests.RequestException as e:
            log.warning(f"      [requests] 連線失敗：{e}")
            time.sleep(2)
    return None


def _scrape_task_requests(task: dict, seen_ids: set) -> list:
    results = []
    page    = 1

    while page <= 50:
        url = SEARCH_URL.format(brand=task["brand"], model=task["model"])
        if page > 1:
            url += f"&p={page}"

        soup = _fetch_with_requests(url)
        if soup is None:
            break

        cards     = soup.select('div.shop-list, li:has(a[href*="carinfo"])')
        new_count = 0
        for card in cards:
            if not card.find("a", href=re.compile(r"carinfo")):
                continue
            car = parse_car_card(card, task["brand"], task["display"])
            if car and car["car_id"] not in seen_ids:
                seen_ids.add(car["car_id"])
                results.append(car)
                new_count += 1

        log.info(f"    [requests] 第 {page} 頁：新增 {new_count} 筆")
        if new_count == 0:
            break

        time.sleep(random.uniform(1.0, 2.5))
        page += 1

    return results


# ──────────────────────────────────────────────
# 策略 2：undetected-chromedriver fallback
# ──────────────────────────────────────────────

def _build_driver():
    if UC_AVAILABLE:
        options = uc.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_experimental_option(
            "prefs", {"profile.managed_default_content_settings.images": 2}
        )
        # 🔥 強制突圍：不等待多餘 JS 廣告載入
        options.page_load_strategy = "none"
        driver = uc.Chrome(options=options, use_subprocess=True)
    else:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        options.add_experimental_option(
            "prefs", {"profile.managed_default_content_settings.images": 2}
        )
        # 🔥 強制突圍：防止 SUM 防火牆卡死頁面載入進度
        options.page_load_strategy = "none"
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(30)
    driver.set_script_timeout(30)

    if not UC_AVAILABLE:
        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
            )
        except Exception:
            pass

    return driver


def _wait_for_list(driver, timeout=20) -> bool:
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
    try:
        return driver.find_element(
            By.CSS_SELECTOR, "li a[href*='carinfo'], div.shop-list a[href*='carinfo']"
        ).get_attribute("href")
    except Exception:
        return ""


def _click_next_page(driver, next_num: int) -> bool:
    xpaths = [
        f"//a[normalize-space(text())='{next_num}']",
        f"//li/a[normalize-space(text())='{next_num}']",
        "//a[contains(@class,'next')]",
        "//a[contains(text(),'下一頁')]",
        "//a[contains(text(),'下頁')]",
        "//a[normalize-space(text())='>']",
    ]
    for xp in xpaths:
        try:
            btn = driver.find_element(By.XPATH, xp)
            driver.execute_script("arguments[0].scrollIntoView(true);", btn)
            driver.execute_script("arguments[0].click();", btn)
            return True
        except NoSuchElementException:
            continue
    return False


def _scrape_task_selenium(driver, task: dict, seen_ids: set) -> list:
    results = []
    page    = 1
    url     = SEARCH_URL.format(brand=task["brand"], model=task["model"])

    try:
        driver.get(url)
    except TimeoutException:
        log.warning("    [selenium] 頁面逾時，嘗試強制介入解析現有 DOM...")
    except WebDriverException as e:
        log.error(f"    [selenium] 載入失敗：{e.__class__.__name__}")
        return results

    time.sleep(random.uniform(1, 3))

    while True:
        if not _wait_for_list(driver, timeout=20):
            log.warning(f"    [selenium] 第 {page} 頁找不到車輛列表 (可能無資料或遭阻擋)")
            break

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
        time.sleep(random.uniform(0.8, 1.5))

        old_href  = _get_first_href(driver)
        # 🔥 已修改為 html.parser
        soup      = BeautifulSoup(driver.page_source, "html.parser")
        cards     = soup.select('div.shop-list, li:has(a[href*="carinfo"])')
        new_count = 0

        for card in cards:
            if not card.find("a", href=re.compile(r"carinfo")):
                continue
            car = parse_car_card(card, task["brand"], task["display"])
            if car and car["car_id"] not in seen_ids:
                seen_ids.add(car["car_id"])
                results.append(car)
                new_count += 1

        log.info(f"    [selenium] 第 {page} 頁：新增 {new_count} 筆")

        if new_count == 0 or page >= 50:
            break
        if not _click_next_page(driver, page + 1):
            log.info("    -> 已達最後一頁")
            break

        time.sleep(random.uniform(2.5, 4.0))

        new_href = _get_first_href(driver)
        if new_href and new_href == old_href:
            log.warning("    ⚠️ 頁面疑似未刷新，額外等待 2 秒...")
            time.sleep(2)

        page += 1

    return results


# ──────────────────────────────────────────────
# 主程式
# ──────────────────────────────────────────────

def run_sum_scraper():
    log.info("[系統] 啟動 SUM 賞車網爬蟲（抗封鎖混合版）...")

    # 探測靜態請求是否可用
    probe_url  = SEARCH_URL.format(brand=SEARCH_TASKS[0]["brand"], model=SEARCH_TASKS[0]["model"])
    use_static = _fetch_with_requests(probe_url) is not None

    if use_static:
        log.info("[系統] 靜態請求暢通，使用 requests 模式（高鐵模式）")
    else:
        log.info("[系統] 靜態請求遭阻擋，啟動 Selenium 裝甲模式突圍")

    valid_cars = []
    seen_ids   = set()

    if use_static:
        for task in SEARCH_TASKS:
            log.info(f"  🔍 搜尋 {task['display']}...")
            cars = _scrape_task_requests(task, seen_ids)
            valid_cars.extend(cars)
            log.info(f"  ✅ {task['display']} 完成，共 {len(cars)} 筆")
            time.sleep(random.uniform(1, 3))
    else:
        driver = _build_driver()
        try:
            for task in SEARCH_TASKS:
                log.info(f"  🔍 搜尋 {task['display']}...")
                cars = _scrape_task_selenium(driver, task, seen_ids)
                valid_cars.extend(cars)
                log.info(f"  ✅ {task['display']} 完成，共 {len(cars)} 筆")
                time.sleep(random.uniform(2, 5))
        finally:
            driver.quit()

    if valid_cars:
        log.info(f"[系統] 掃描完畢，總計入庫 {len(valid_cars)} 筆。")
        db_manager.update_listings("SUM", valid_cars)
    else:
        log.error("[系統] 未抓取到任何資料。")


if __name__ == "__main__":
    run_sum_scraper()
