from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json
import re

def scrape_8891_homepage():
    with sync_playwright() as p:
        # Launch Chromium headless
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Navigate to the 8891 homepage
        page.goto('https://www.8891.com.tw/')

        # Wait for the main app to load and network to idle to ensure React has rendered
        page.wait_for_selector('#app')
        page.wait_for_load_state('networkidle')

        html = page.content()
        soup = BeautifulSoup(html, "html.parser")

        data = {
            "top_brands": [],
            "quick_search_prices": [],
            "news_and_articles": []
        }

        # Extract top brands for new cars
        for a in soup.find_all("a", href=lambda h: h and "/Models" in h and "search" not in h):
            text = a.get_text(strip=True)
            href = a.get("href")
            # Filter out generic links
            if text and text not in ["新車", "全部", "推薦", "全部車款", "其他條件", "車款大全"]:
                 data["top_brands"].append({"brand": text, "url": href})

        # Extract price ranges
        for a in soup.find_all("a", href=lambda h: h and "/Models/search" in h):
            text = a.get_text(strip=True)
            href = a.get("href")
            if text and "萬" in text:
                 data["quick_search_prices"].append({"price_range": text, "url": href})

        # Extract news or articles from slick carousel
        for slide in soup.find_all(class_=lambda c: c and "slick-slide" in c):
            text = slide.get_text(strip=True)
            # Find the anchor inside
            a = slide.find("a")
            url = a.get("href") if a else ""

            # Avoid duplicates and overly long concatenated text blocks
            if text and len(text) > 5 and len(text) < 100 and not any(article["title"] == text for article in data["news_and_articles"]):
                data["news_and_articles"].append({"title": text, "url": url})

        # Print the extracted data as formatted JSON
        print(json.dumps(data, indent=2, ensure_ascii=False))

        browser.close()

if __name__ == "__main__":
    scrape_8891_homepage()
