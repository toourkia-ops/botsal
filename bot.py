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
# 4 Kanalın ID'sini bu listeye ekle
KANAL_ID_LISTESI = ["@Amazon_indirim_tr"]
STORE_ID = "amazonind0133-21"
AMAZON_SEARCH_URL = "https://www.amazon.com.tr/s?k={query}&tag={tag}"
# =================================================================

logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

class AmazonBot:
    def __init__(self):
        self.shared_urls = set()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9",
        }

    def clean_amazon_url(self, url):
        pid = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
        if pid:
            return f"https://www.amazon.com.tr/dp/{pid.group(1)}?tag={STORE_ID}"
        return url

    async def scrape_product(self, url):
        async with httpx.AsyncClient(headers=self.headers, follow_redirects=True, timeout=25) as client:
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    print(f"⚠️ HATA DETAYI: Amazon {r.status_code} hatası. Bot engellenmiş olabilir.")
                    return None

                soup = BeautifulSoup(r.text, "html.parser")
                title = soup.find("span", {"id": "productTitle"})
                title = title.get_text(strip=True) if title else "Harika Bir Ürün"
                
                price_tag = soup.find("span", {"class": "a-price-whole"})
                price = price_tag.get_text(strip=True) if price_tag else "Fiyat Bilgisi Yok"
                
                img_tag = soup.find("img", {"id": "landingImage"})
                img_url = img_tag.get("src") if img_tag else None
                
                return {"title": title, "price": price, "img_url": img_url, "link": self.clean_amazon_url(url)}
            except Exception as e:
                print(f"❌ SCRAPE HATASI: {e}")
                return None

    async def post_to_all_channels(self, bot, data):
        """Tüm kanallara sırayla mesaj gönderir."""
        caption = f"🔥 **{data['title'][:100]}...**\n\n💰 **Fiyat:** {data['price']} TL\n\n👇 **Satın Al:**"
        keyboard = [[InlineKeyboardButton("📦 Sitede Gör", url=data['link'])]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        for kanal_id in KANAL_ID_LISTESI:
            try:
                await bot.send_photo(
                    chat_id=kanal_id,
                    photo=data['img_url'],
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup
                )
                logger.info(f"✅ Ürün {kanal_id} kanalında paylaşıldı.")
                await asyncio.sleep(2) # Kanallar arası kısa bekleme (Telegram banlamasın)
            except Exception as e:
                logger.error(f"❌ {kanal_id} kanalına gönderilemedi: {e}")

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /ara command to generate Amazon affiliate search links."""
    if not update.message or not context.args:
        await update.message.reply_text("🔎 Lütfen aramak istediğin ürünü yaz: `/ara oyuncu faresi`", parse_mode=ParseMode.MARKDOWN)
        return

    query = " ".join(context.args)
    # Boşlukları + yapalım ve diğer karakterleri encode edelim
    cleaned_query = urllib.parse.quote_plus(query)
    
    # Link oluşturma
    search_url = AMAZON_SEARCH_URL.format(query=cleaned_query, tag=STORE_ID)
    
    # Yanıt
    response_text = f"🔍 İşte aradığın ürün için en iyi fırsatlar!\n\n👉 [Buraya Tıklayarak İncele]({search_url})"
    
    await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)

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
                        clean_url = bot_engine.clean_amazon_url(full_url)
                        
                        if clean_url not in bot_engine.shared_urls:
                            data = await bot_engine.scrape_product(clean_url)
                            if data and data['img_url']:
                                await bot_engine.post_to_all_channels(application.bot, data)
                                bot_engine.shared_urls.add(clean_url)
                                count += 1
                                await asyncio.sleep(10) # Amazon spam koruması
                        if count >= 3: break
                else:
                    print(f"⚠️ HATA DETAYI: Kod {r.status_code}")
        except Exception as e:
            print(f"🚨 DÖNGÜ HATASI: {e}")
        
        await asyncio.sleep(1800) # 30 dk bekle

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
                await bot_engine.post_to_all_channels(app.bot, data)
                await update.message.reply_text("✅ Tüm kanallarda paylaşıldı!")
            else:
                await update.message.reply_text("❌ Ürün bilgisi çekilemedi. Terminali kontrol et.")

    # Handlers
    app.add_handler(CommandHandler("ara", handle_search))
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Bot Aktif! Kanalları takip ediyorum.")))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), manual_msg))

    # Start auto loop
    asyncio.create_task(auto_loop(bot_engine, app))

    print("🚀 Bot Hazır!")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    
    # Keep running
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot durduruldu.")
    except Exception as e:
        print(f"BAĞLANTI KOPTU! Kritik Hata: {e}")
