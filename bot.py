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
                    logger.warning(f"⚠️ HATA DETAYI: Amazon {r.status_code} hatası.")
                    return None

                soup = BeautifulSoup(r.text, "html.parser")
                
                # Title
                title_tag = soup.find("span", {"id": "productTitle"})
                title = title_tag.get_text(strip=True) if title_tag else "Harika Bir Ürün"
                
                # Current Price
                price_tag = soup.find("span", {"class": "a-price-whole"})
                price_str = price_tag.get_text(strip=True).replace(".", "").replace(",", "") if price_tag else "0"
                current_price = float(price_str) if price_str.isdigit() else 0
                
                # List Price (Eski Fiyat)
                list_price_tag = soup.find("span", {"class": "a-price a-text-price"})
                list_price_str = "0"
                if list_price_tag:
                    list_price_val = list_price_tag.find("span", {"class": "a-offscreen"})
                    if list_price_val:
                        list_price_str = list_price_val.get_text(strip=True).replace("₺", "").replace(".", "").replace(",", "").strip()
                
                list_price = float(list_price_str) if list_price_str.isdigit() else 0
                
                # Seller (Satıcı)
                merchant_info = soup.find("div", {"id": "merchant-info"})
                seller_text = merchant_info.get_text(strip=True) if merchant_info else ""
                is_amazon_seller = "Amazon.com.tr" in seller_text
                
                # Discount Calculation
                discount_rate = 0
                if list_price > current_price and list_price > 0:
                    discount_rate = ((list_price - current_price) / list_price) * 100
                
                img_tag = soup.find("img", {"id": "landingImage"})
                img_url = img_tag.get("src") if img_tag else None
                
                return {
                    "title": title, 
                    "price": current_price, 
                    "list_price": list_price,
                    "discount_rate": discount_rate,
                    "is_amazon_seller": is_amazon_seller,
                    "img_url": img_url, 
                    "link": self.clean_amazon_url(url)
                }
            except Exception as e:
                logger.error(f"❌ SCRAPE HATASI: {e}")
                return None

    async def post_to_all_channels(self, bot, data):
        """Tüm kanallara sırayla mesaj gönderir."""
        # Dip Fiyat Alarmı
        alarm = "🚨 **DİP FİYAT ALARMI** 🚨\n\n" if data['discount_rate'] >= 30 else ""
        
        caption = (
            f"{alarm}"
            f"🔥 **{data['title'][:100]}...**\n\n"
            f"💰 **Fiyat:** {data['price']:,.2f} TL\n"
            f"📉 **İndirim Oranı:** %{int(data['discount_rate'])}\n\n"
            f"👇 **Satın Al:**"
        )
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
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"❌ {kanal_id} kanalına gönderilemedi: {e}")

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /ara command to generate Amazon affiliate search links with a premium look."""
    if not update.message:
        return

    # Komutun yanında kelime yoksa uyarı ver
    if not context.args:
        await update.message.reply_text(
            "🔍 **Arama Asistanı Devrede!**\n\n"
            "Aradığın ürünü bulmak için komutu şu şekilde kullan:\n"
            "`/ara oyuncu klavyesi`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Kelime grubunu al ve temizle
    query = " ".join(context.args)
    # Boşlukları + yapalım
    cleaned_query = urllib.parse.quote_plus(query)
    
    # Affiliate Link Şablonu
    search_url = f"https://www.amazon.com.tr/s?k={cleaned_query}&tag={STORE_ID}"
    
    # Premium Mesaj Tasarımı
    response_text = (
        f"💎 **Amazon Arama Asistanı**\n\n"
        f"✨ **Aranan Ürün:** `{query}`\n"
        f"🚀 Senin için en uygun sonuçları hazırladım!"
    )
    
    # Şık bir buton ekleyelim
    keyboard = [[InlineKeyboardButton("🛍️ Ürünleri Gör ve İncele", url=search_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        response_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

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
                                # AŞAMA 2: FİLTRELER
                                if data['discount_rate'] >= 15 and data['is_amazon_seller']:
                                    await bot_engine.post_to_all_channels(application.bot, data)
                                    bot_engine.shared_urls.add(clean_url)
                                    count += 1
                                    await asyncio.sleep(10) # Amazon spam koruması
                                else:
                                    logger.info(f"⏭️ Ürün filtrelendi: %{int(data['discount_rate'])} indirim, Satıcı Amazon mu: {data['is_amazon_seller']}")
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
                # Manuel mesajda da filtreleri uygulayalım mı? Kullanıcı "Amazon'dan çekilen fırsatlar" dediği için 
                # manuel girişte kullanıcıyı bilgilendirmek daha şık olur.
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
