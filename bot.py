import asyncio
import logging
import sys
import httpx
import re
import urllib.parse
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from telegram.constants import ParseMode

# ================= CONFIG (BURALARI DOLDUR KANKA) =================
TOKEN = "8769910441:AAHb0Y_jFaHBPWYKK-PT_QCh47RPLCyA3jw"
KANAL_MAP = {
    "elektronik": "@Elektronik_Kanal_ID",
    "ev_yasam": "@Ev_Yasam_Kanal_ID",
    "genel": "@Amazon_indirim_tr"
}
SEYAHAT_KEYWORDS = ["valiz", "powerbank", "termos", "boyun yastığı", "adaptör", "sırt çantası", "şarj", "kulaklık", "pasaport"]
TOURKIA_DB = "tourkia_deals.json"
DB_NAME = "bot_data.db"

STORE_ID = "amazonind0133-21"
AMAZON_SEARCH_URL = "https://www.amazon.com.tr/s?k={query}&tag={tag}"
# =================================================================

import json
import os
import sqlite3
from datetime import datetime

logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

class AmazonBot:
    def __init__(self):
        self.shared_urls = set()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9",
        }
        self.init_db()

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
        """Fiyatı kaydeder ve en düşük fiyat bilgisini döner."""
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        
        # Geçmişe ekle
        c.execute("INSERT INTO price_history VALUES (?, ?, ?)", (asin, current_price, datetime.now()))
        
        # Mevcut en düşük fiyatı kontrol et
        c.execute("SELECT lowest_price FROM products WHERE asin = ?", (asin,))
        row = c.fetchone()
        
        is_lowest = False
        if row:
            old_lowest = row[0]
            if current_price < old_lowest:
                c.execute("UPDATE products SET lowest_price = ?, title = ? WHERE asin = ?", (current_price, title, asin))
                is_lowest = True
            elif current_price == old_lowest:
                is_lowest = True
        else:
            c.execute("INSERT INTO products VALUES (?, ?, ?)", (asin, title, current_price))
            is_lowest = True
            old_lowest = current_price
            
        conn.commit()
        conn.close()
        return is_lowest, (row[0] if row else current_price)

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
        clean_url, asin = self.clean_amazon_url(url)
        async with httpx.AsyncClient(headers=self.headers, follow_redirects=True, timeout=25) as client:
            try:
                r = await client.get(clean_url)
                if r.status_code != 200:
                    logger.warning(f"⚠️ HATA DETAYI: Amazon {r.status_code} hatası.")
                    return None

                soup = BeautifulSoup(r.text, "html.parser")
                
                title_tag = soup.find("span", {"id": "productTitle"})
                title = title_tag.get_text(strip=True) if title_tag else "Harika Bir Ürün"
                
                price_tag = soup.find("span", {"class": "a-price-whole"})
                price_str = price_tag.get_text(strip=True).replace(".", "").replace(",", "") if price_tag else "0"
                current_price = float(price_str) if price_str.isdigit() else 0
                
                list_price_tag = soup.find("span", {"class": "a-price a-text-price"})
                list_price_str = "0"
                if list_price_tag:
                    list_price_val = list_price_tag.find("span", {"class": "a-offscreen"})
                    if list_price_val:
                        list_price_str = list_price_val.get_text(strip=True).replace("₺", "").replace(".", "").replace(",", "").strip()
                
                list_price = float(list_price_str) if list_price_str.isdigit() else 0
                
                merchant_info = soup.find("div", {"id": "merchant-info"})
                seller_text = merchant_info.get_text(strip=True) if merchant_info else ""
                is_amazon_seller = "Amazon.com.tr" in seller_text
                
                category = "genel"
                breadcrumb = soup.find("div", {"id": "wayfinding-breadcrumbs_container"})
                if breadcrumb:
                    b_text = breadcrumb.get_text(strip=True).lower()
                    if any(x in b_text for x in ["elektronik", "bilgisayar", "teknoloji", "telefon"]):
                        category = "elektronik"
                    elif any(x in b_text for x in ["ev", "yaşam", "mutfak", "mobilya"]):
                        category = "ev_yasam"
                
                discount_rate = 0
                if list_price > current_price and list_price > 0:
                    discount_rate = ((list_price - current_price) / list_price) * 100
                
                img_tag = soup.find("img", {"id": "landingImage"})
                img_url = img_tag.get("src") if img_tag else None
                
                # AŞAMA 5 İÇİN HAZIRLIK: Fiyat geçmişini güncelle
                is_lowest, last_lowest = self.update_price_history(asin, title, current_price)
                
                return {
                    "asin": asin,
                    "title": title, 
                    "price": current_price, 
                    "list_price": list_price,
                    "discount_rate": discount_rate,
                    "is_amazon_seller": is_amazon_seller,
                    "category": category,
                    "img_url": img_url, 
                    "link": clean_url,
                    "is_lowest": is_lowest,
                    "last_lowest": last_lowest
                }
            except Exception as e:
                logger.error(f"❌ SCRAPE HATASI: {e}")
                return None

    async def post_to_all_channels(self, bot, data):
        """Kategoriye göre doğru kanala mesaj gönderir."""
        alarm = "🚨 **DİP FİYAT ALARMI** 🚨\n\n" if data['discount_rate'] >= 30 else ""
        
        # Fiyat Geçmişi Notu
        history_note = ""
        if data['is_lowest']:
            history_note = "\n💎 **Bu Ürün İçin Tespit Edilen En Düşük Fiyat!**"
        else:
            history_note = f"\n📉 *Daha önceki en düşük fiyat: {data['last_lowest']:,.2f} TL*"

        caption = (
            f"{alarm}"
            f"🔥 **{data['title'][:100]}...**\n\n"
            f"💰 **Fiyat:** {data['price']:,.2f} TL\n"
            f"📉 **İndirim Oranı:** %{int(data['discount_rate'])}\n"
            f"🏷️ **Kategori:** {data['category'].replace('_', ' ').title()}"
            f"{history_note}\n\n"
            f"👇 **Satın Al:**"
        )
        keyboard = [[InlineKeyboardButton("📦 Sitede Gör", url=data['link'])]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        target_channel = KANAL_MAP.get(data['category'], KANAL_MAP['genel'])

        try:
            await bot.send_photo(
                chat_id=target_channel,
                photo=data['img_url'],
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
            logger.info(f"✅ Ürün {target_channel} kanalında paylaşıldı. (Kategori: {data['category']})")
            
            if any(k in data['title'].lower() for k in SEYAHAT_KEYWORDS):
                self.save_to_tourkia(data)
            
            # AŞAMA 2: Alarm Kontrolü
            await self.check_and_notify_alerts(bot, data)
                
        except Exception as e:
            logger.error(f"❌ {target_channel} kanalına gönderilemedi: {e}")

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /ara command to generate Amazon affiliate search links with a premium look."""
    if not update.message:
        return

    if not context.args:
        await update.message.reply_text(
            "🔍 **Arama Asistanı Devrede!**\n\n"
            "Aradığın ürünü bulmak için komutu şu şekilde kullan:\n"
            "`/ara oyuncu klavyesi`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    query = " ".join(context.args)
    cleaned_query = urllib.parse.quote_plus(query)
    search_url = f"https://www.amazon.com.tr/s?k={cleaned_query}&tag={STORE_ID}"
    
    response_text = (
        f"💎 **Amazon Arama Asistanı**\n\n"
        f"✨ **Aranan Ürün:** `{query}`\n"
        f"🚀 Senin için en uygun sonuçları hazırladım!"
    )
    
    keyboard = [[InlineKeyboardButton("🛍️ Ürünleri Gör ve İncele", url=search_url)]]
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
                                if data['discount_rate'] >= 15 and data['is_amazon_seller']:
                                    await bot_engine.post_to_all_channels(application.bot, data)
                                    bot_engine.shared_urls.add(clean_url)
                                    count += 1
                                    await asyncio.sleep(10)
                                else:
                                    logger.info(f"⏭️ Ürün filtrelendi: %{int(data['discount_rate'])} indirim, Satıcı Amazon mu: {data['is_amazon_seller']}")
                        if count >= 3: break
                else:
                    print(f"⚠️ HATA DETAYI: Kod {r.status_code}")
        except Exception as e:
            print(f"🚨 DÖNGÜ HATASI: {e}")
        
        await asyncio.sleep(1800)

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
                if data['discount_rate'] < 15:
                    await update.message.reply_text(f"⚠️ Bu ürünün indirim oranı %{int(data['discount_rate'])}. Minimum %15 gerekli.")
                    return
                if not data['is_amazon_seller']:
                    await update.message.reply_text("⚠️ Bu ürün Amazon.com.tr tarafından satılmıyor, güvenlik gereği paylaşılmadı.")
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


