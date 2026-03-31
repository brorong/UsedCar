import re
import requests
from bs4 import BeautifulSoup
import time
import db_manager  # 🔥 載入資料庫總管

# ── 系統設定與白名單 ──────────────────────────────────────────────────────
BASE_URL = "https://auto.8891.com.tw"
SEARCH_URL = "https://auto.8891.com.tw/?keyword={keyword}&page={page}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://auto.8891.com.tw/",
}
# 🔥 v3 競品全線開戰名單
TARGET_CARS = {
    "Luxgen": ["n7", "URX", "U6"],
    "Toyota": ["bZ4X"],
    "Honda": ["CR-V"],
    "Ford": ["Kuga"],
    "Nissan": ["X-Trail"],
    "Hyundai": ["Santa Fe"]
}
TW_CITIES = "台北市|新北市|桃園市|新竹縣|新竹市|基隆市|宜蘭縣|台中市|彰化縣|雲林縣|苗栗縣|南投縣|台南市|高雄市|嘉義市|嘉義縣|屏東縣|台東縣|花蓮縣|澎湖縣|金門縣|連江縣"


# ── 數值清洗工具 ──────────────────────────────────────────────────────────
def clean_price(raw_str):
    if not raw_str or "電洽" in raw_str: return None, "inquiry"
    raw_str = raw_str.replace(",", "").replace(" ", "").replace("\n", "")
    m = re.search(r"([\d.]+)", raw_str)
    if m:
        val = float(m.group(1))
        return int(val * 10000) if "萬" in raw_str else int(val), "normal"
    return None, "inquiry"


def clean_mileage(raw_str):
    if not raw_str: return -1
    raw_str = raw_str.replace(",", "").replace(" ", "").replace("\n", "")
    m = re.search(r"([\d.]+)", raw_str)
    if m:
        val = float(m.group(1))
        return int(val * 10000) if "萬" in raw_str else int(val)
    return -1


def identify_brand_model(text):
    text_upper = text.upper()
    for brand, models in TARGET_CARS.items():
        for model in models:
            if model.upper() in text_upper: return brand, model
    return None, None


# ── 爬蟲解析邏輯 ──────────────────────────────────────────────────────────
RE_YEAR_KM = re.compile(r"((?:19|20)\d{2})\s*年\s*([\d.]+\s*萬公里|[\d,]+\s*公里)")
RE_LOCATION = re.compile(rf"({TW_CITIES})")
RE_FALLBACK_PRICE = re.compile(r"([\d.]+)\s*萬(?!\s*公里)")


def parse_car_card(card):
    data = {"platform": "8891", "car_id": "", "brand": "", "model": "", "title": "", "year": 0, "mileage": 0,
            "price": None, "price_type": "inquiry", "location": "未知", "url": ""}
    href = card.get("href", "")
    if not href: return None
    data["url"] = href if href.startswith("http") else BASE_URL + href

    id_span = card.find('span', attrs={'data-id': True})
    if id_span:
        data["car_id"] = id_span.get("data-id")
    else:
        id_m = re.search(r"usedauto-infos-(\d+)\.html", data["url"])
        data["car_id"] = id_m.group(1) if id_m else card.get("data-id", "")
    if not data["car_id"]: return None

    raw_text = card.get_text(" ", strip=True)
    raw_text = re.sub(r"\s{2,}", " ", raw_text)
    brand, model = identify_brand_model(raw_text)
    if not brand or not model: return None

    # 🔥 核心修改：在此處將品牌與車型強制轉為大寫字母
    data["brand"] = brand.upper()
    data["model"] = model.upper()

    m_yk = RE_YEAR_KM.search(raw_text)
    if m_yk:
        data["year"] = int(m_yk.group(1))
        data["mileage"] = clean_mileage(m_yk.group(2))
    m_loc = RE_LOCATION.search(raw_text)
    if m_loc: data["location"] = m_loc.group(1)

    # 精確定位價格 HTML 標籤
    price_tag = card.find(class_=re.compile(r"listItem_ib-price"))
    if price_tag:
        price_text = price_tag.get_text(strip=True)
        data["price"], data["price_type"] = clean_price(price_text)
    else:
        # 備用方案
        m_price = RE_FALLBACK_PRICE.search(raw_text)
        if m_price:
            data["price"], data["price_type"] = clean_price(m_price.group(1) + "萬")
        elif "電洽" in raw_text:
            data["price"], data["price_type"] = clean_price("電洽")

    return data


# ── 主爬蟲排程邏輯 ────────────────────────────────────────────────────────
def run_scraper():
    print("[系統] 開始針對指定車款抓取 8891 平台...")
    session = requests.Session()
    valid_cars = []

    # 跨車型全域去重
    global_seen_ids = set()

    for brand, models in TARGET_CARS.items():
        for model in models:
            page = 1
            model_car_count = 0
            while True:
                url = SEARCH_URL.format(keyword=model, page=page)
                try:
                    resp = session.get(url, headers=HEADERS, timeout=10)
                    resp.raise_for_status()
                    soup = BeautifulSoup(resp.text, "html.parser")
                    cards = soup.select('a[href*="usedauto-infos"]')
                    if not cards: break

                    new_cars_on_this_page = 0
                    for card in cards:
                        car_data = parse_car_card(card)

                        # 檢查這台車在「本次所有搜尋中」是否已經出現過
                        if car_data and car_data["car_id"] not in global_seen_ids:
                            global_seen_ids.add(car_data["car_id"])
                            valid_cars.append(car_data)
                            new_cars_on_this_page += 1
                            model_car_count += 1

                    if new_cars_on_this_page == 0: break
                    page += 1
                    time.sleep(1.5)
                except Exception as e:
                    break
            print(f"  ✅ {model} 總共抓取 {model_car_count} 筆")

    # 交給總管處理籌碼流動
    if valid_cars:
        db_manager.update_listings("8891", valid_cars)


if __name__ == "__main__":
    run_scraper()
