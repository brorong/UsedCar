import requests
import time
import db_manager  # 🔥 載入資料庫總管

# ── 系統設定 ──────────────────────────────────────────────────────────
API_URL = "https://www.abccar.com.tw/apiv2/Search/GetIndexAPI"
BASE_URL = "https://www.abccar.com.tw"

# 🔥 v3 競品全線開戰名單
TARGET_CARS = {
    "Luxgen": ["N7", "URX", "U6"],
    "Toyota": ["BZ4X"],
    "Honda": ["CR-V"],
    "Ford": ["KUGA"],
    "Nissan": ["X-TRAIL"],
    "Hyundai": ["SANTA FE"]
}

HEADERS = {
    'accept': 'application/json, text/plain, */*',
    'content-type': 'application/json',
    'origin': 'https://www.abccar.com.tw',
    'referer': 'https://www.abccar.com.tw/search',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
}


# ── 核心解析邏輯 ──────────────────────────────────────────────────────────
def normalize_car_data(item, brand, model_name):
    """將 API 原始資料轉換為標準資料庫格式"""
    car_id = str(item.get('carID', ''))
    if not car_id: return None

    # 取得里程數 (API 通常直接給整數)
    mileage = item.get('mileage', 0)

    # 處理地點 (臺轉台)
    location = str(item.get('countryName', '未知')).replace("臺", "台")

    # 處理價格
    raw_price = item.get('price')
    price = None
    price_type = "INQUIRY"

    if raw_price and raw_price > 0:
        # API 回傳的 price 通常已經是萬元為單位的整數，例如 88 代表 88萬
        # 為了跟資料庫對齊，我們統一轉為「元」
        price = int(raw_price * 10000)
        price_type = "NORMAL"

    return {
        "platform": "ABC",
        "car_id": car_id,
        "brand": brand.upper(),
        "model": model_name.upper(),
        "title": item.get('carModelDisplayName', ''),
        "year": int(item.get('manufactureYear', 0)),
        "mileage": int(mileage),
        "price": price,
        "price_type": price_type,
        "location": location,
        "url": f"{BASE_URL}/car/{car_id}"
    }


# ── 主爬蟲排程 ────────────────────────────────────────────────────────────
def run_abc_scraper():
    print("[系統] 啟動 ABC 好車網「API 直攻噴射版」...")
    valid_cars = []
    seen_ids = set()

    # 這裡不需要 Selenium 了，直接用 requests
    session = requests.Session()

    for brand, models in TARGET_CARS.items():
        for model in models:
            page = 1
            model_total = 0
            print(f"  🔍 搜尋車型: {model}...")

            while True:
                payload = {
                    "page": page,
                    "searchType": "1",
                    "keyword": model,
                    "tab": 1,
                    "orderByField": "0",
                    "orderSort": False,
                    "inSide": True
                }

                try:
                    response = session.post(API_URL, headers=HEADERS, json=payload, timeout=10)
                    response.raise_for_status()
                    data = response.json()

                    # 根據你提供的 JSON 結構提取
                    car_list = data.get("carList", {}).get("carConditionList", [])

                    if not car_list:
                        break  # 這代表抓完了

                    new_on_page = 0
                    for item in car_list:
                        norm = normalize_car_data(item, brand, model)
                        if norm and norm["car_id"] not in seen_ids:
                            seen_ids.add(norm["car_id"])
                            valid_cars.append(norm)
                            new_on_page += 1
                            model_total += 1

                    if new_on_page == 0:
                        break  # 該頁都是重複資料，停止

                    print(f"    📄 第 {page} 頁抓取成功，取得 {new_on_page} 筆新車。")
                    page += 1
                    time.sleep(1)  # 溫柔一點，間隔 1 秒

                except Exception as e:
                    print(f"    ⚠️ 請求錯誤: {e}")
                    break

            print(f"  ✅ {model} 掃描完成，累計抓取 {model_total} 筆。")

    if valid_cars:
        print(f"\n🎉 ABC 掃描總計: {len(valid_cars)} 筆。寫入資料庫...")
        db_manager.update_listings("ABC", valid_cars)


if __name__ == "__main__":
    run_abc_scraper()
