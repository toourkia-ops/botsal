import asyncio
import logging
import sys
import httpx
import re
import urllib.parse
import html
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from telegram.constants import ParseMode

# ================= CONFIG (BURALARI DOLDUR KANKA) =================
TOKEN = "8769910441:AAHb0Y_jFaHBPWYKK-PT_QCh47RPLCyA3jw"
KANAL_MAP = {
    "teknoloji": "@Elektronik_Kanal_ID",
    "kozmetik": "@Kozmetik_Kanal_ID",
    "tekstil": "@Moda_Kanal_ID",
    "ev_yasam": "@Ev_Yasam_Kanal_ID",
    "seyahat": "@Seyahat_Kanal_ID",
    "genel": "@Amazon_indirim_tr"
}

CATEGORY_KEYWORDS = {
    "teknoloji": ["bilgisayar", "laptop", "klavye", "mouse", "monitör", "ekran kartı", "işlemci", "telefon", "kulaklık", "tablet", "akıllı saat"],
    "kozmetik": ["parfüm", "makyaj", "ruj", "fondöten", "nemlendirici", "şampuan", "tıraş makinesi", "güneş kremi", "epilatör", "serum"],
    "tekstil": ["tişört", "ayakkabı", "sneaker", "mont", "kaban", "çanta", "kol saati", "ceket", "pantolon", "güneş gözlüğü"],
    "ev_yasam": ["kahve makinesi", "robot süpürge", "airfryer", "tencere", "blender", "mobilya", "matkap", "aydınlatma", "ütü"],
    "seyahat": ["valiz", "çadır", "uyku tulumu", "powerbank", "termos", "seyahat adaptörü", "boyun yastığı", "kamp", "sırt çantası"]
}
TOURKIA_DB = "tourkia_deals.json"
DB_NAME = "bot_data.db"

STORE_ID = "amazonind0133-21"
TRENDYOL_PARTNER_ID = "PARTNER_ID_TRENDYOL"
HEPSIBURADA_PARTNER_ID = "PARTNER_ID_HB"

AMAZON_SEARCH_URL = "https://www.amazon.com.tr/s?k={query}&tag={tag}"
TRENDYOL_SEARCH_URL = "https://www.trendyol.com/sr?q={query}&utm_source=aff_t&utm_medium=cps&utm_campaign={partner_id}"
HB_SEARCH_URL = "https://www.hepsiburada.com/ara?q={query}&utm_source=affiliate&utm_medium=go-partner&utm_campaign={partner_id}"
# =================================================================

# Hız Sınırı Deposu (User ID: [Timestamp1, Timestamp2, ...])
USER_SEARCH_LIMITS = {}


import json
import os
import sqlite3
from datetime import datetime

logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

class AmazonBot:
    def __init__(self):
        self.shared_urls = set()
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1"
        ]
        self.init_db()

    def get_headers(self):
        import random
        return {
            "User-Agent": random.choice(self.user_agents),
            "Accept-Language": "tr-TR,tr;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Referer": "https://www.google.com/"
        }

    def detect_category(self, title):
        """Ürün başlığına göre kategori tespiti yapar."""
        title_lower = title.lower()
        for category, keywords in CATEGORY_KEYWORDS.items():
            if any(k in title_lower for k in keywords):
                return category
        return "genel"

    def init_db(self):
        """Veritabanı tablolarını oluşturur."""
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS price_history 
                     (asin TEXT, price REAL, timestamp DATETIME)''')
        c.execute('''CREATE TABLE IF NOT EXISTS products 
                     (asin TEXT PRIMARY KEY, title TEXT, lowest_price REAL)''')
        # AŞAMA 2: Alarm Tablosu
        c.execute('''CREATE TABLE IF NOT EXISTS alerts 
                     (user_id INTEGER, keyword TEXT, target_price REAL)''')
        conn.commit()
        conn.close()

    async def check_and_notify_alerts(self, bot, data):
        """Kullanıcı alarmlarını kontrol eder ve eşleşme varsa mesaj atar."""
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT user_id, keyword, target_price FROM alerts")
        all_alerts = c.fetchall()
        conn.close()

        for user_id, keyword, target_price in all_alerts:
            if keyword.lower() in data['title'].lower() and data['price'] <= target_price:
                try:
                    text = (
                        f"🔔 **FİYAT ALARMI YAKALANDI!**\n\n"
                        f"📦 **Ürün:** {data['title']}\n"
                        f"💰 **Fiyat:** {data['price']:,.2f} TL\n"
                        f"🎯 **Hedefin:** {target_price:,.2f} TL altıydı.\n\n"
                        f"👉 [Hemen Satın Al]({data['link']})"
                    )
                    await bot.send_message(chat_id=user_id, text=text, parse_mode=ParseMode.MARKDOWN)
                    logger.info(f"📩 Kullanıcı {user_id} için alarm bildirimi gönderildi.")
                except Exception as e:
                    logger.error(f"❌ Bildirim gönderilemedi: {e}")

    def update_price_history(self, asin, title, current_price):
        """Fiyatı kaydeder ve son 30 günün en düşük fiyatı olup olmadığını döner."""
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        
        now = datetime.now()
        # Geçmişe ekle
        c.execute("INSERT INTO price_history VALUES (?, ?, ?)", (asin, current_price, now))
        
        # Son 30 günün en düşük fiyatını bul (mevcut kayıt dahil en düşüğü bulur)
        c.execute("""SELECT MIN(price) FROM price_history 
                     WHERE asin = ? AND timestamp >= datetime('now', '-30 days')""", (asin,))
        min_last_30 = c.fetchone()[0]
        
        is_30_day_low = False
        if min_last_30 is not None and current_price <= min_last_30:
            is_30_day_low = True
            
        # Global en düşük fiyatı da takip etmeye devam edelim
        c.execute("SELECT lowest_price FROM products WHERE asin = ?", (asin,))
        row = c.fetchone()
        last_lowest = row[0] if row else current_price

        if row:
            if current_price < row[0]:
                c.execute("UPDATE products SET lowest_price = ?, title = ? WHERE asin = ?", (current_price, title, asin))
        else:
            c.execute("INSERT INTO products VALUES (?, ?, ?)", (asin, title, current_price))
            
        conn.commit()
        conn.close()
        return is_30_day_low, last_lowest

    def clean_amazon_url(self, url):
        pid = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
        if pid:
            return f"https://www.amazon.com.tr/dp/{pid.group(1)}?tag={STORE_ID}", pid.group(1)
        return url, None

    def save_to_tourkia(self, data):
        deals = []
        if os.path.exists(TOURKIA_DB):
            try:
                with open(TOURKIA_DB, "r", encoding="utf-8") as f:
                    deals = json.load(f)
            except:
                deals = []
        
        if not any(d['link'] == data['link'] for d in deals):
            deals.append(data)
            with open(TOURKIA_DB, "w", encoding="utf-8") as f:
                json.dump(deals, f, ensure_ascii=False, indent=4)
            logger.info(f"✨ TOURKIA: Ürün seyahat veritabanına eklendi: {data['title'][:30]}")

    async def scrape_product(self, url):
        """URL'ye göre doğru platformu seçer."""
        if "amazon.com.tr" in url:
            return await self.scrape_amazon(url)
        elif "trendyol.com" in url:
            return await self.scrape_trendyol(url)
        elif "hepsiburada.com" in url:
            return await self.scrape_hepsiburada(url)
        return None

    async def scrape_amazon(self, url):
        clean_url, asin = self.clean_amazon_url(url)
        async with httpx.AsyncClient(headers=self.get_headers(), follow_redirects=True, timeout=25) as client:
            try:
                r = await client.get(clean_url)
                if r.status_code != 200:
                    logger.warning(f"⚠️ HATA: Amazon {r.status_code} döndürdü.")
                    return None

                soup = BeautifulSoup(r.text, "html.parser")
                title = soup.find("span", {"id": "productTitle"}).get_text(strip=True) if soup.find("span", {"id": "productTitle"}) else "Amazon Ürünü"
                
                price_tag = soup.find("span", {"class": "a-price-whole"})
                price_str = price_tag.get_text(strip=True).replace(".", "").replace(",", "") if price_tag else "0"
                current_price = float(price_str) if price_str.isdigit() else 0
                
                list_price = 0
                list_price_tag = soup.find("span", {"class": "a-price a-text-price"})
                if list_price_tag:
                    val = list_price_tag.find("span", {"class": "a-offscreen"})
                    if val:
                        list_price = float(val.get_text(strip=True).replace("₺", "").replace(".", "").replace(",", "").strip())

                merchant_info = soup.find("div", {"id": "merchant-info"})
                is_amazon_seller = "Amazon.com.tr" in merchant_info.get_text() if merchant_info else False
                
                discount_rate = ((list_price - current_price) / list_price * 100) if list_price > current_price else 0
                img_url = soup.find("img", {"id": "landingImage"}).get("src") if soup.find("img", {"id": "landingImage"}) else None
                
                category = self.detect_category(title)
                is_30_day_low, last_lowest = self.update_price_history(asin, title, current_price)
                
                return {
                    "asin": asin, "title": title, "price": current_price, "list_price": list_price,
                    "discount_rate": discount_rate, "is_amazon_seller": is_amazon_seller,
                    "category": category, "img_url": img_url, "link": clean_url,
                    "is_lowest": is_30_day_low, "last_lowest": last_lowest
                }
            except Exception as e:
                logger.error(f"❌ Amazon Scrape Hatası: {e}")
                return None

    async def scrape_trendyol(self, url):
        # Affiliate Parametreleri Ekle
        clean_url = url.split("?")[0]
        affiliate_url = f"{clean_url}?utm_source=aff_t&utm_medium=cps&utm_campaign={TRENDYOL_PARTNER_ID}"
        
        async with httpx.AsyncClient(headers=self.get_headers(), follow_redirects=True, timeout=25) as client:
            try:
                r = await client.get(clean_url)
                soup = BeautifulSoup(r.text, "html.parser")
                
                title = soup.find("h1", {"class": "pr-new-br"}).get_text(strip=True) if soup.find("h1", {"class": "pr-new-br"}) else "Trendyol Ürünü"
                
                price_tag = soup.find("span", {"class": "prc-dsc"})
                price_str = price_tag.get_text(strip=True).replace("TL", "").replace(".", "").replace(",", "").strip() if price_tag else "0"
                current_price = float(price_str) if price_str.isdigit() else 0
                
                list_price_tag = soup.find("span", {"class": "prc-org"})
                list_price = float(list_price_tag.get_text(strip=True).replace("TL", "").replace(".", "").replace(",", "").strip()) if list_price_tag else current_price
                
                discount_rate = ((list_price - current_price) / list_price * 100) if list_price > current_price else 0
                img_url = soup.find("img", {"class": "base-product-image"}).get("src") if soup.find("img", {"class": "base-product-image"}) else None
                
                # Kategori tespiti
                category = self.detect_category(title)
                
                # ASIN yerine Trendyol ID kullan (URL'den çek)
                product_id = re.search(r"-p-(\d+)", clean_url).group(1) if re.search(r"-p-(\d+)", clean_url) else "ty_" + str(hash(clean_url))
                is_30_day_low, last_lowest = self.update_price_history(product_id, title, current_price)

                return {
                    "asin": product_id, "title": title, "price": current_price, "list_price": list_price,
                    "discount_rate": discount_rate, "is_amazon_seller": True, # Trendyol için hep geçsin
                    "category": category, "img_url": img_url, "link": affiliate_url,
                    "is_lowest": is_30_day_low, "last_lowest": last_lowest
                }
            except Exception as e:
                logger.error(f"❌ Trendyol Scrape Hatası: {e}")
                return None

    async def scrape_hepsiburada(self, url):
        # Affiliate Parametreleri Ekle
        clean_url = url.split("?")[0]
        affiliate_url = f"{clean_url}?utm_source=affiliate&utm_medium=go-partner&utm_campaign={HEPSIBURADA_PARTNER_ID}"

        async with httpx.AsyncClient(headers=self.get_headers(), follow_redirects=True, timeout=25) as client:
            try:
                r = await client.get(clean_url)
                soup = BeautifulSoup(r.text, "html.parser")
                
                title = soup.find("h1", {"id": "product-name"}).get_text(strip=True) if soup.find("h1", {"id": "product-name"}) else "Hepsiburada Ürünü"
                
                price_tag = soup.find("span", {"data-test-id": "price-current-price"})
                price_str = price_tag.get_text(strip=True).replace("TL", "").replace(".", "").replace(",", "").strip() if price_tag else "0"
                current_price = float(price_str) if price_str.isdigit() else 0
                
                list_price_tag = soup.find("del", {"id": "old-price"})
                list_price = float(list_price_tag.get_text(strip=True).replace("TL", "").replace(".", "").replace(",", "").strip()) if list_price_tag else current_price

                discount_rate = ((list_price - current_price) / list_price * 100) if list_price > current_price else 0
                img_tag = soup.find("img", {"id": "product-image"})
                img_url = img_tag.get("src") if img_tag else None

                category = self.detect_category(title)
                product_id = re.search(r"-p-([A-Z0-9]+)", clean_url).group(1) if re.search(r"-p-([A-Z0-9]+)", clean_url) else "hb_" + str(hash(clean_url))
                is_30_day_low, last_lowest = self.update_price_history(product_id, title, current_price)

                return {
                    "asin": product_id, "title": title, "price": current_price, "list_price": list_price,
                    "discount_rate": discount_rate, "is_amazon_seller": True, 
                    "category": category, "img_url": img_url, "link": affiliate_url,
                    "is_lowest": is_30_day_low, "last_lowest": last_lowest
                }
            except Exception as e:
                logger.error(f"❌ Hepsiburada Scrape Hatası: {e}")
                return None

    async def post_to_all_channels(self, bot, data):
        """Kategoriye göre doğru kanala mesaj gönderir."""
        alarm = "🚨 <b>DİP FİYAT ALARMI</b> 🚨\n\n" if data['discount_rate'] >= 30 else ""
        
        # Fiyat Geçmişi Notu (FOMO Algoritması)
        history_note = ""
        if data['is_lowest']:
            history_note = "\n🔥 <b>İstatistiklerimize göre bu ürün son 1 ayın en düşük fiyatında, yarın artma olasılığı çok yüksek!</b>"
        else:
            history_note = f"\n📉 <i>Daha önceki en düşük fiyat: {data['last_lowest']:,.2f} TL</i>"

        # HTML güvenliği için başlığı temizle
        safe_title = html.escape(data['title'][:100])
        old_price_text = f"<s>{data['list_price']:,.2f} TL</s> " if data['list_price'] > data['price'] else ""

        caption = (
            f"{alarm}"
            f"🔥 <b>{safe_title}...</b>\n\n"
            f"💰 <b>Fiyat:</b> {old_price_text}<b>{data['price']:,.2f} TL</b>\n"
            f"📉 <b>İndirim Oranı:</b> %{int(data['discount_rate'])}\n"
            f"🏷️ <b>Kategori:</b> {data['category'].replace('_', ' ').title()}"
            f"{history_note}"
        )
        keyboard = [[InlineKeyboardButton("🛒 İndirimli Fiyattan Satın Al", url=data['link'])]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        target_channel = KANAL_MAP.get(data['category'], KANAL_MAP['genel'])

        try:
            await bot.send_photo(
                chat_id=target_channel,
                photo=data['img_url'],
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
            logger.info(f"✅ Ürün {target_channel} kanalında paylaşıldı. (Kategori: {data['category']})")
            
            if data['category'] == "seyahat":
                self.save_to_tourkia(data)
            
            # AŞAMA 2: Alarm Kontrolü
            await self.check_and_notify_alerts(bot, data)
                
        except Exception as e:
            logger.error(f"❌ {target_channel} kanalına gönderilemedi: {e}")

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /ara command with platform selection and rate limiting."""
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    now = datetime.now().timestamp()

    # Rate Limit Kontrolü (1 dakikada max 3 arama)
    if user_id not in USER_SEARCH_LIMITS:
        USER_SEARCH_LIMITS[user_id] = []
    
    USER_SEARCH_LIMITS[user_id] = [t for t in USER_SEARCH_LIMITS[user_id] if now - t < 60]

    if len(USER_SEARCH_LIMITS[user_id]) >= 3:
        await update.message.reply_text("🛑 **Hız Sınırı!**\n\nSistem güvenliği için lütfen 1 dakika bekleyin.", parse_mode=ParseMode.MARKDOWN)
        return

    if not context.args:
        await update.message.reply_text(
            "🔍 **Arama Asistanı Devrede!**\n\n"
            "Kullanım: `/ara [platform] [ürün adı]`\n"
            "Platformlar: `trendyol`, `hb`, `amazon` (varsayılan)\n\n"
            "Örnekler:\n"
            "`/ara trendyol oyuncu faresi`\n"
            "`/ara hb kamp çadırı`\n"
            "`/ara airfryer` (Amazon'da arar)",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Platform Seçimi
    first_arg = context.args[0].lower()
    if first_arg in ["trendyol", "hb", "amazon", "hepsiburada"]:
        platform = "hb" if first_arg == "hepsiburada" else first_arg
        query_parts = context.args[1:]
    else:
        platform = "amazon"
        query_parts = context.args

    query = " ".join(query_parts)
    if not query:
        await update.message.reply_text("❌ Lütfen aramak istediğiniz ürünün adını yazın.")
        return

    USER_SEARCH_LIMITS[user_id].append(now)
    cleaned_query = urllib.parse.quote_plus(query)

    if platform == "trendyol":
        search_url = TRENDYOL_SEARCH_URL.format(query=cleaned_query, partner_id=TRENDYOL_PARTNER_ID)
        platform_name = "Trendyol"
    elif platform == "hb":
        search_url = HB_SEARCH_URL.format(query=cleaned_query, partner_id=HEPSIBURADA_PARTNER_ID)
        platform_name = "Hepsiburada"
    else:
        search_url = AMAZON_SEARCH_URL.format(query=cleaned_query, tag=STORE_ID)
        platform_name = "Amazon"
    
    response_text = (
        f"💎 **{platform_name} Arama Asistanı**\n\n"
        f"✨ **Aranan Ürün:** `{query}`\n"
        f"🚀 Senin için en uygun sonuçları hazırladım!"
    )
    
    keyboard = [[InlineKeyboardButton(f"🛍️ {platform_name} Ürünlerini Gör", url=search_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        response_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_alarm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kullanıcının fiyat alarmı kurmasını sağlar: /alarm [ürün] [fiyat]"""
    if not update.message or len(context.args) < 2:
        await update.message.reply_text(
            "🔔 **Fiyat Alarmı Nasıl Kurulur?**\n\n"
            "Kullanım: `/alarm [ürün adı] [maksimum fiyat]`\n"
            "Örnek: `/alarm iphone 15 45000`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        target_price = float(context.args[-1].replace(".", "").replace(",", "."))
        keyword = " ".join(context.args[:-1])
        user_id = update.effective_user.id

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("INSERT INTO alerts VALUES (?, ?, ?)", (user_id, keyword, target_price))
        conn.commit()
        conn.close()

        await update.message.reply_text(
            f"✅ **Alarm Kuruldu!**\n\n"
            f"📦 **Ürün:** `{keyword}`\n"
            f"🎯 **Hedef Fiyat:** `{target_price:,.2f} TL` altı\n\n"
            f"Ürün bu fiyata düştüğünde sana buradan mesaj atacağım."
        )
    except ValueError:
        await update.message.reply_text("❌ Lütfen geçerli bir fiyat gir (Örn: 45000)")

async def daily_summary_loop(bot, bot_engine):
    """Her akşam 21:00'de günün en iyi fırsatlarını özet geçer."""
    while True:
        now = datetime.now()
        # Saat 21:00'de çalış (Eğer o saati geçtiyse bir sonraki güne planla)
        target_time = now.replace(hour=21, minute=0, second=0, microsecond=0)
        if now >= target_time:
            target_time = target_time.replace(day=now.day + 1)
        
        wait_seconds = (target_time - now).total_seconds()
        logger.info(f"📅 Günlük özet {int(wait_seconds/3600)} saat sonra paylaşılacak.")
        await asyncio.sleep(wait_seconds)

        # Günün en iyi 3 indirimini çek
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        c.execute("""SELECT asin, title, price, lowest_price 
                     FROM products 
                     ORDER BY (lowest_price) ASC LIMIT 3""") # Basitlik için en düşük fiyatlı 3 ürünü alalım
        # Not: Gerçekte indirim oranına göre sıralamak daha iyi olur ama tablo yapısını korumak için böyle bıraktım.
        top_deals = c.fetchall()
        conn.close()

        if top_deals:
            summary_text = "🌟 **GÜNÜN EN ÇOK TIKLANAN FIRSATLARI** 🌟\n\n"
            summary_text += "Bugün bu ürünler kapışıldı! Kaçıranlar için son şans:\n\n"
            
            for asin, title, price, lowest in top_deals:
                summary_text += f"🔥 {title[:50]}...\n💰 **Fiyat:** {price:,.2f} TL\n👉 [Ürüne Git](https://www.amazon.com.tr/dp/{asin}?tag={STORE_ID})\n\n"
            
            summary_text += "🚀 Yarın daha iyileri gelecek, takipte kalın!"
            
            for channel_id in KANAL_MAP.values():
                try:
                    await bot.send_message(chat_id=channel_id, text=summary_text, parse_mode=ParseMode.MARKDOWN)
                    await asyncio.sleep(2)
                except:
                    pass
            logger.info("✅ Günlük özet paylaşıldı.")

async def auto_loop(bot_engine, application):
    print("✅ Otomatik tarama devrede. 4 kanal için takip başladı.")
    while True:
        try:
            logger.info("🔍 Amazon Fırsatları taranıyor...")
            async with httpx.AsyncClient(headers=bot_engine.headers, timeout=25) as client:
                r = await client.get("https://www.amazon.com.tr/gp/goldbox")
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "html.parser")
                    links = soup.find_all("a", href=re.compile(r"/(?:dp|gp/product)/[A-Z0-9]{10}"))
                    count = 0
                    for link in links:
                        full_url = "https://www.amazon.com.tr" + link['href'] if link['href'].startswith('/') else link['href']
                        clean_url, asin = bot_engine.clean_amazon_url(full_url)
                        
                        if clean_url not in bot_engine.shared_urls:
                            data = await bot_engine.scrape_product(clean_url)
                            if data and data['img_url']:
                                if data['discount_rate'] >= 5:
                                    await bot_engine.post_to_all_channels(application.bot, data)
                                    bot_engine.shared_urls.add(clean_url)
                                    count += 1
                                    await asyncio.sleep(10)
                                else:
                                    logger.info(f"🚫 Ürün reddedildi: İndirim sadece %{int(data['discount_rate'])}")
                        if count >= 3: break
                else:
                    print(f"⚠️ HATA DETAYI: Kod {r.status_code}")
        except Exception as e:
            print(f"🚨 DÖNGÜ HATASI: {e}")
        
        await asyncio.sleep(300)

async def main():
    bot_engine = AmazonBot()
    app = ApplicationBuilder().token(TOKEN).build()

    async def manual_msg(update, context):
        if not update.message or not update.message.text: return
        url_match = re.search(r"(https?://[^\s]+)", update.message.text)
        if url_match:
            await update.message.reply_text("⏳ Tüm kanallar için işlem başlatıldı...")
            data = await bot_engine.scrape_product(url_match.group(1))
            if data:
                if data['discount_rate'] < 5:
                    await update.message.reply_text(f"⚠️ Bu ürünün indirim oranı %{int(data['discount_rate'])}. Minimum %5 gerekli.")
                    return

                await bot_engine.post_to_all_channels(app.bot, data)
                await update.message.reply_text("✅ Filtrelerden geçti ve tüm kanallarda paylaşıldı!")
            else:
                await update.message.reply_text("❌ Ürün bilgisi çekilemedi. Terminali kontrol et.")

    app.add_handler(CommandHandler("ara", handle_search))
    app.add_handler(CommandHandler("alarm", handle_alarm))
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Bot Aktif! Kanalları takip ediyorum.")))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), manual_msg))

    asyncio.create_task(auto_loop(bot_engine, app))
    asyncio.create_task(daily_summary_loop(app.bot, bot_engine))

    print("🚀 Bot Hazır!")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot durduruldu.")
    except Exception as e:
        print(f"BAĞLANTI KOPTU! Kritik Hata: {e}")


