"""
the Lwe - P2P Exchange Telegram Bot for Ethiopia (Amharic + English)
Stack: pyTelegramBotAPI (telebot) + PostgreSQL / Supabase (psycopg2-binary)

Features
  * Item swap marketplace + automatic wishlist alerts
  * Mandatory official-channel join step after language selection
  * Social Media Exchange with Admin Escrow (admin = secure middleman)

Run:  python main.py
"""
import html
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import telebot
from dotenv import load_dotenv
from psycopg2 import Error as PgError
from psycopg2 import InterfaceError, OperationalError, pool
from psycopg2.extras import RealDictCursor
from telebot import types

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("the_lwe")

# Environment variables first. If your host cannot set env vars, replace the
# placeholder text after "or" with your real values (keep the quotes).
BOT_TOKEN = os.getenv("BOT_TOKEN") or "PASTE_YOUR_BOT_TOKEN_HERE"
DATABASE_URL = os.getenv("DATABASE_URL") or "PASTE_YOUR_SUPABASE_DATABASE_URL_HERE"
ADMIN_ID = int(os.getenv("ADMIN_ID", "8829761469"))  # escrow middleman

CHANNEL_URL = "https://t.me/thetrusLwe"
CHANNEL_USERNAME = "@thetrusLwe"

# False = every social listing must be approved by the admin before it goes live.
SOCIAL_AUTO_APPROVE = False

if BOT_TOKEN.startswith("PASTE_") or DATABASE_URL.startswith("PASTE_"):
    raise SystemExit("Please set BOT_TOKEN and DATABASE_URL (environment variables or the top of main.py).")

telebot.apihelper.RETRY_ON_ERROR = True
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)

MAX_PHOTOS = 5
states = {}       # uid -> {"step": str, "data": dict}
list_ctx = {}     # uid -> {"ids": [...]}  item browse/search results
social_ctx = {}   # uid -> {"ids": [...]}  social listing results
last_query = {}   # uid -> last search keyword
lang_cache = {}   # uid -> "en" | "am"


def esc(s):
    return html.escape(str(s if s is not None else ""))


def run_async(fn, *args):
    """Run slow work (notifications) in a background thread."""
    threading.Thread(target=fn, args=args, daemon=True).start()


# ----------------------------------------------------------------------------
# Catalogs
# ----------------------------------------------------------------------------
CATEGORIES = {
    "phones": ("📱 Phones", "📱 ስልኮች"),
    "computers": ("💻 Computers & Laptops", "💻 ኮምፒውተር እና ላፕቶፕ"),
    "electronics": ("🔌 Electronics", "🔌 ኤሌክትሮኒክስ"),
    "clothing": ("👕 Clothing", "👕 ልብስ"),
    "shoes": ("👟 Shoes", "👟 ጫማ"),
    "furniture": ("🛋 Furniture", "🛋 የቤት ዕቃ"),
    "books": ("📚 Books", "📚 መጻሕፍት"),
    "vehicles": ("🚗 Vehicles", "🚗 ተሽከርካሪ"),
    "home": ("🏠 Home & Kitchen", "🏠 የቤትና ማብሰያ ዕቃ"),
    "other": ("📦 Other", "📦 ሌላ"),
}

PLATFORMS = {
    "tiktok": ("🎵 TikTok", "🎵 TikTok"),
    "facebook": ("📘 Facebook", "📘 Facebook"),
    "telegram": ("✈️ Telegram", "✈️ Telegram"),
    "youtube": ("▶️ YouTube", "▶️ YouTube"),
    "instagram": ("📸 Instagram", "📸 Instagram"),
    "x": ("🐦 X (Twitter)", "🐦 X (Twitter)"),
    "snapchat": ("👻 Snapchat", "👻 Snapchat"),
    "other": ("🌍 Other", "🌍 ሌላ"),
}

DEAL_TYPES = ("sell", "swap", "buy")


def cat_name(key, lang):
    c = CATEGORIES.get(key)
    return (c[0] if lang == "en" else c[1]) if c else (key or "-")


def plat_name(key, lang):
    p = PLATFORMS.get(key)
    return (p[0] if lang == "en" else p[1]) if p else (key or "-")


# ----------------------------------------------------------------------------
# Translations
# ----------------------------------------------------------------------------
CHOOSE_LANG = "🌐 Choose your language / ቋንቋ ይምረጡ"
JOIN_BTN = "📢 Join Channel / ቻናልን ይቀላቀሉ"
CONTINUE_BTN = "✅ Continue / ቀጥል"

T = {
    "en": {
        "welcome_brand": (
            "✨ <b>Welcome to the Lwe</b> ✨\n\n"
            "Ethiopia's modern marketplace for smart swaps 🇪🇹\n\n"
            "🔄 Trade what you don't need for what you want\n"
            "🔔 Get instant alerts the moment your dream item appears\n"
            "🌐 Buy, sell &amp; swap social media accounts with admin escrow protection\n\n"
            "Just a couple of quick steps and you're in 👇"
        ),
        "join_prompt": "📢 <b>Step 1 · Join our official channel</b>\nGet news, updates and safe-trading tips, then tap <b>Continue</b>.",
        "join_required": "⚠️ Please join the channel first, then tap Continue.",
        "join_ok": "🎉 Awesome, welcome aboard!\n<b>Step 2 · Let's create your profile.</b>",
        "ask_name": "👤 Enter your full name:",
        "ask_phone": "📱 Enter your phone number (e.g. 0912345678 or +251912345678) or tap the button below:",
        "share_phone": "📱 Share my phone",
        "bad_phone": "❌ Invalid phone number. Please enter an Ethiopian number (09... or +251...).",
        "ask_location": "📍 Enter your City / Subcity (e.g. Addis Ababa, Bole):",
        "registered": "✅ You're all set! Welcome to the Lwe family 🎉",
        "menu_title": "🏠 <b>the Lwe</b> · Main menu",
        "m_post": "📦 Post Item",
        "m_browse": "🔍 Browse Items",
        "m_search": "🔎 Search",
        "m_social": "🌐 Social Media Exchange",
        "m_items": "🗂 My Items",
        "m_alerts": "🔔 My Alerts",
        "m_profile": "👤 Profile",
        "m_lang": "🌐 አማርኛ",
        "cancel": "❌ Cancel",
        "cancelled": "Cancelled.",
        "p_title": "📝 Enter the item title:",
        "p_desc": "📄 Write a description of the item:",
        "p_cat": "🗂 Choose a category:",
        "p_cond": "⚙️ Item condition:",
        "cond_new": "🆕 New",
        "cond_used": "♻️ Used",
        "p_photos": "📸 Send photo(s) of the item (up to 5). Tap Done when finished.",
        "p_done": "✅ Done",
        "p_skip": "⏭ Continue without photos",
        "p_photo_added": "📸 Photos added: {n}/5",
        "p_need_photo": "Please send a photo, or use the buttons.",
        "p_value": "💰 Enter the estimated value in ETB (numbers only):",
        "bad_number": "❌ Please enter a valid number.",
        "p_wanted": "🔄 What would you like in exchange?",
        "p_saved": "🎉 Your item has been posted!",
        "text_needed": "Please send text.",
        "lbl_cat": "Category",
        "lbl_cond": "Condition",
        "lbl_value": "Est. value",
        "lbl_wanted": "Wants in exchange",
        "lbl_loc": "Location",
        "st_swapped": "✅ <b>SWAPPED</b>",
        "b_choose_cat": "🔍 Choose a category to browse:",
        "cat_all": "📋 All categories",
        "b_none": "😕 No items found here yet.",
        "btn_alert": "🔔 Set Alert / የፍላጎት ማስታወቂያ መዝግብ",
        "btn_offer": "🔄 Offer Swap",
        "btn_photos": "🖼 All photos",
        "expired": "This list has expired. Please browse or search again.",
        "s_ask": "🔎 Type what you are looking for (e.g. iPhone 13, Laptop, Shoes):",
        "s_none": "😕 No items found for «{q}».\nWant to be notified when someone posts it?",
        "a_ask_kw": "🔔 Type the keyword you want alerts for (e.g. iPhone 13):",
        "a_saved": "✅ Alert saved! I will notify you when a matching item is posted.",
        "a_exists": "ℹ️ You already have this alert.",
        "a_list_title": "🔔 <b>Your alerts</b> (tap to delete):",
        "a_empty": "You have no alerts yet.",
        "a_add_btn": "➕ Add alert",
        "a_deleted": "🗑 Alert deleted.",
        "n_match": "🔔 <b>New item matching your alert!</b>\n\n",
        "o_own": "❌ This is your own item.",
        "o_unavailable": "❌ This item is no longer available.",
        "o_pick": "🔄 Which of your items do you want to offer?",
        "o_none_item": "💬 No item – just a message",
        "o_msg": "✍️ Write a message to the owner (what you offer, meeting place, etc.):",
        "o_sent": "✅ Your offer was sent to the owner. You will be notified of the answer.",
        "o_received": "📩 <b>New swap offer!</b>\n\n👤 From: {name}\n📍 {loc}\n\n🎯 For your item: <b>{item}</b>\n🎁 Offered item: {offered}\n\n💬 {msg}",
        "o_no_item": "— (message only)",
        "o_accept": "✅ Accept",
        "o_decline": "❌ Decline",
        "o_accepted_owner": "✅ Offer accepted! Contact the other person:\n\n👤 {name}\n📞 {phone}\n📍 {loc}",
        "o_accepted_offerer": "🎉 Your offer for <b>{item}</b> was accepted!\n\n👤 {name}\n📞 {phone}\n📍 {loc}\n\nArrange the swap safely and meet in a public place.",
        "o_declined": "😕 Your offer for <b>{item}</b> was declined.",
        "o_declined_owner": "Offer declined.",
        "o_already": "This offer was already answered.",
        "mi_empty": "You have not posted any items yet.",
        "btn_edit": "✏️ Edit",
        "btn_delete": "🗑 Delete",
        "btn_swapped": "✅ Mark swapped",
        "btn_yes": "✅ Yes",
        "btn_no": "↩️ No",
        "mi_confirm": "⚠️ Delete this item permanently?",
        "mi_deleted": "🗑 Item deleted.",
        "mi_swapped": "✅ Marked as swapped.",
        "ed_choose": "✏️ What do you want to edit?",
        "f_title": "Title",
        "f_description": "Description",
        "f_est_value": "Value",
        "f_wanted_item": "Wanted",
        "ed_ask": "Send the new value:",
        "ed_done": "✅ Updated.",
        "pf_text": "👤 <b>Your profile</b>\n\nName: {name}\nPhone: {phone}\nLocation: {loc}\nLanguage: English",
        "lang_changed": "✅ Language changed to English.",
        "help": "ℹ️ <b>Commands</b>\n/start – start / main menu\n/menu – main menu\n/language – change language\n/cancel – cancel current action",
        "error": "⚠️ Something went wrong. Please try again.",
        # ---- social media exchange ----
        "so_menu": "🌐 <b>Social Media Exchange</b>\nBuy, sell or swap accounts safely — our admin acts as the escrow middleman between both sides.\n\nWhat would you like to do?",
        "so_post_btn": "➕ Post / Request",
        "so_browse_btn": "🔍 Browse accounts",
        "so_mine_btn": "📋 My posts",
        "so_platform": "🌐 Choose the platform:",
        "so_type": "What do you want to do?",
        "dt_sell": "💰 Sell",
        "dt_swap": "🔄 Swap",
        "dt_buy": "🛒 Want to buy",
        "so_handle": "🔗 Enter the account name or link (only the admin will see this):",
        "so_followers": "👥 How many followers / subscribers? (e.g. 12000 or 12k)",
        "so_desc": "📄 Describe the account (niche, age, engagement, monetization...):",
        "so_desc_buy": "📄 Describe what you are looking for (niche, minimum followers, etc.):",
        "so_price": "💰 Enter the asking price in ETB (numbers only):",
        "so_price_buy": "💰 Enter your budget in ETB (numbers only):",
        "so_wanted": "🔄 What do you want in exchange? (e.g. a Facebook page with 5k followers)",
        "so_submitted_pending": "✅ Submitted! Our admin will review your post and contact you. It goes live after approval.",
        "so_submitted_active": "✅ Your post is live! The admin has been notified and will act as escrow.",
        "so_approved": "🎉 Your listing #{id} was approved and is now live!",
        "so_rejected": "❌ Your listing #{id} was not approved. Contact the admin for details.",
        "so_none": "😕 No accounts found here yet.",
        "so_btn_deal": "🤝 Start Escrow Deal",
        "so_deal_msg": "✍️ Write your offer for the admin/escrow (price you offer, account you would swap, notes):",
        "so_deal_sent": "✅ Request received! Our admin (escrow) will contact you and the other party shortly.\n\n⚠️ Never pay or hand over account access directly to the other person.",
        "so_deal_owner": "📩 Someone is interested in your {platform} listing #{id}!\nOur admin (escrow middleman) will contact you shortly to arrange the deal safely.",
        "so_deal_done": "🎉 Deal #{id} was completed successfully! Thank you for trading with the Lwe.",
        "so_deal_cancelled": "❌ Deal #{id} was cancelled by the admin.",
        "so_deal_exists": "ℹ️ You already have an open deal for this post.",
        "so_unavailable": "❌ This post is no longer available.",
        "so_own": "❌ This is your own post.",
        "so_mine_empty": "You have no social media posts yet.",
        "so_st_pending": "⏳ Pending approval",
        "so_st_active": "🟢 Live",
        "so_st_rejected": "🔴 Rejected",
        "so_st_sold": "✅ Completed",
        "so_delete": "🗑 Delete",
        "so_deleted": "🗑 Post deleted.",
        "so_hidden": "🔒 Account details are shared securely by the admin during the deal.",
        "so_choose_platform": "🔍 Choose a platform to browse:",
        "lbl_platform": "Platform",
        "lbl_followers": "Followers",
        "lbl_price": "Price",
        "lbl_budget": "Budget",
    },
    "am": {
        "welcome_brand": (
            "✨ <b>እንኳን ወደ the Lwe በደህና መጡ</b> ✨\n\n"
            "የኢትዮጵያ ዘመናዊ የልውውጥ መድረክ 🇪🇹\n\n"
            "🔄 የማያስፈልግዎትን በሚፈልጉት ይለውጡ\n"
            "🔔 የሚፈልጉት እቃ ሲለጠፍ ወዲያውኑ ማሳወቂያ ያግኙ\n"
            "🌐 የሶሻል ሚዲያ አካውንቶችን በአስተዳዳሪ ዋስትና (Escrow) በአስተማማኝ ይግዙ፣ ይሽጡ፣ ይለዋወጡ\n\n"
            "ጥቂት ፈጣን ደረጃዎች ብቻ ይቀራሉ 👇"
        ),
        "join_prompt": "📢 <b>ደረጃ 1 · ኦፊሴላዊ ቻናላችንን ይቀላቀሉ</b>\nዜናዎችን፣ ማሻሻያዎችንና የአስተማማኝ ግብይት ምክሮችን ያግኙ፤ ከዚያ <b>ቀጥል</b> ይጫኑ።",
        "join_required": "⚠️ እባክዎ መጀመሪያ ቻናሉን ይቀላቀሉ፤ ከዚያ ቀጥል ይጫኑ።",
        "join_ok": "🎉 ድንቅ ነው፣ እንኳን ደህና መጡ!\n<b>ደረጃ 2 · መገለጫዎን እንፍጠር።</b>",
        "ask_name": "👤 ሙሉ ስምዎን ያስገቡ፦",
        "ask_phone": "📱 ስልክ ቁጥርዎን ያስገቡ (ለምሳሌ 0912345678 ወይም +251912345678) ወይም ከታች ያለውን ቁልፍ ይጫኑ፦",
        "share_phone": "📱 ስልኬን አጋራ",
        "bad_phone": "❌ ልክ ያልሆነ ስልክ ቁጥር። እባክዎ የኢትዮጵያ ቁጥር (09... ወይም +251...) ያስገቡ።",
        "ask_location": "📍 ከተማ/ክፍለ ከተማዎን ያስገቡ (ለምሳሌ አዲስ አበባ፣ ቦሌ)፦",
        "registered": "✅ ሁሉም ዝግጁ ነው! ወደ the Lwe ቤተሰብ እንኳን ደህና መጡ 🎉",
        "menu_title": "🏠 <b>the Lwe</b> · ዋና ማውጫ",
        "m_post": "📦 እቃ ለጥፍ",
        "m_browse": "🔍 እቃዎችን ተመልከት",
        "m_search": "🔎 ፈልግ",
        "m_social": "🌐 የሶሻል ሚዲያ ልውውጥ",
        "m_items": "🗂 የኔ እቃዎች",
        "m_alerts": "🔔 የኔ ማስታወቂያዎች",
        "m_profile": "👤 መገለጫ",
        "m_lang": "🌐 English",
        "cancel": "❌ ሰርዝ",
        "cancelled": "ተሰርዟል።",
        "p_title": "📝 የእቃውን ርዕስ ያስገቡ፦",
        "p_desc": "📄 የእቃውን መግለጫ ይጻፉ፦",
        "p_cat": "🗂 ምድብ ይምረጡ፦",
        "p_cond": "⚙️ የእቃው ሁኔታ፦",
        "cond_new": "🆕 አዲስ",
        "cond_used": "♻️ ያገለገለ",
        "p_photos": "📸 የእቃውን ፎቶ(ዎች) ይላኩ (እስከ 5)። ሲጨርሱ «ጨርሻለሁ» ይጫኑ።",
        "p_done": "✅ ጨርሻለሁ",
        "p_skip": "⏭ ያለ ፎቶ ቀጥል",
        "p_photo_added": "📸 የተጨመሩ ፎቶዎች፦ {n}/5",
        "p_need_photo": "እባክዎ ፎቶ ይላኩ ወይም ቁልፎቹን ይጠቀሙ።",
        "p_value": "💰 የሚገመተውን ዋጋ በብር (ETB) ያስገቡ (ቁጥር ብቻ)፦",
        "bad_number": "❌ እባክዎ ትክክለኛ ቁጥር ያስገቡ።",
        "p_wanted": "🔄 በምን እቃ መለወጥ ይፈልጋሉ?",
        "p_saved": "🎉 እቃዎ ተለጥፏል!",
        "text_needed": "እባክዎ ጽሑፍ ይላኩ።",
        "lbl_cat": "ምድብ",
        "lbl_cond": "ሁኔታ",
        "lbl_value": "ግምታዊ ዋጋ",
        "lbl_wanted": "የሚፈለግ",
        "lbl_loc": "አካባቢ",
        "st_swapped": "✅ <b>ተለውጧል</b>",
        "b_choose_cat": "🔍 ለማየት ምድብ ይምረጡ፦",
        "cat_all": "📋 ሁሉም ምድቦች",
        "b_none": "😕 እስካሁን እዚህ ምንም እቃ የለም።",
        "btn_alert": "🔔 Set Alert / የፍላጎት ማስታወቂያ መዝግብ",
        "btn_offer": "🔄 የልውውጥ ጥያቄ አቅርብ",
        "btn_photos": "🖼 ሁሉንም ፎቶዎች",
        "expired": "ይህ ዝርዝር ጊዜው አልፏል። እባክዎ እንደገና ይፈልጉ።",
        "s_ask": "🔎 የሚፈልጉትን ይጻፉ (ለምሳሌ iPhone 13፣ ላፕቶፕ፣ ጫማ)፦",
        "s_none": "😕 ለ«{q}» ምንም እቃ አልተገኘም።\nአንድ ሰው ሲለጥፍ እንዲያሳውቅዎ ይፈልጋሉ?",
        "a_ask_kw": "🔔 ማስታወቂያ የሚፈልጉበትን ቃል ይጻፉ (ለምሳሌ iPhone 13)፦",
        "a_saved": "✅ ማስታወቂያ ተመዝግቧል! ተመሳሳይ እቃ ሲለጠፍ አሳውቅዎታለሁ።",
        "a_exists": "ℹ️ ይህ ማስታወቂያ አስቀድሞ አለዎት።",
        "a_list_title": "🔔 <b>የእርስዎ ማስታወቂያዎች</b> (ለመሰረዝ ይጫኑ)፦",
        "a_empty": "እስካሁን ምንም ማስታወቂያ የለዎትም።",
        "a_add_btn": "➕ ማስታወቂያ ጨምር",
        "a_deleted": "🗑 ማስታወቂያው ተሰርዟል።",
        "n_match": "🔔 <b>ከፍላጎትዎ ጋር የሚመሳሰል አዲስ እቃ ተለጥፏል!</b>\n\n",
        "o_own": "❌ ይህ የራስዎ እቃ ነው።",
        "o_unavailable": "❌ ይህ እቃ አሁን የለም።",
        "o_pick": "🔄 ከእርስዎ እቃዎች የትኛውን ማቅረብ ይፈልጋሉ?",
        "o_none_item": "💬 እቃ ሳይኖር – መልዕክት ብቻ",
        "o_msg": "✍️ ለባለቤቱ መልዕክት ይጻፉ (ምን እንደሚያቀርቡ፣ መገናኛ ቦታ ወዘተ)፦",
        "o_sent": "✅ ጥያቄዎ ለባለቤቱ ተልኳል። መልሱን አሳውቅዎታለሁ።",
        "o_received": "📩 <b>አዲስ የልውውጥ ጥያቄ!</b>\n\n👤 ከ፦ {name}\n📍 {loc}\n\n🎯 ለእቃዎ፦ <b>{item}</b>\n🎁 የቀረበ እቃ፦ {offered}\n\n💬 {msg}",
        "o_no_item": "— (መልዕክት ብቻ)",
        "o_accept": "✅ ተቀበል",
        "o_decline": "❌ አልቀበልም",
        "o_accepted_owner": "✅ ጥያቄው ተቀባይነት አግኝቷል! ሌላውን ሰው ያግኙ፦\n\n👤 {name}\n📞 {phone}\n📍 {loc}",
        "o_accepted_offerer": "🎉 ለ<b>{item}</b> ያቀረቡት ጥያቄ ተቀባይነት አግኝቷል!\n\n👤 {name}\n📞 {phone}\n📍 {loc}\n\nልውውጡን በጥንቃቄ ያካሂዱ፤ በሕዝብ ቦታ ይገናኙ።",
        "o_declined": "😕 ለ<b>{item}</b> ያቀረቡት ጥያቄ አልተቀበለም።",
        "o_declined_owner": "ጥያቄው ተቀባይነት አላገኘም።",
        "o_already": "ይህ ጥያቄ አስቀድሞ ምላሽ አግኝቷል።",
        "mi_empty": "እስካሁን ምንም እቃ አልለጠፉም።",
        "btn_edit": "✏️ አስተካክል",
        "btn_delete": "🗑 ሰርዝ",
        "btn_swapped": "✅ ተለውጧል ብለህ ምልክት አድርግ",
        "btn_yes": "✅ አዎ",
        "btn_no": "↩️ አይ",
        "mi_confirm": "⚠️ ይህ እቃ ለዘላለም ይሰረዝ?",
        "mi_deleted": "🗑 እቃው ተሰርዟል።",
        "mi_swapped": "✅ እንደተለወጠ ምልክት ተደርጓል።",
        "ed_choose": "✏️ ምን ማስተካከል ይፈልጋሉ?",
        "f_title": "ርዕስ",
        "f_description": "መግለጫ",
        "f_est_value": "ዋጋ",
        "f_wanted_item": "የሚፈለግ",
        "ed_ask": "አዲሱን እሴት ይላኩ፦",
        "ed_done": "✅ ተስተካክሏል።",
        "pf_text": "👤 <b>መገለጫዎ</b>\n\nስም፦ {name}\nስልክ፦ {phone}\nአካባቢ፦ {loc}\nቋንቋ፦ አማርኛ",
        "lang_changed": "✅ ቋንቋ ወደ አማርኛ ተቀይሯል።",
        "help": "ℹ️ <b>ትዕዛዞች</b>\n/start – ጀምር / ዋና ማውጫ\n/menu – ዋና ማውጫ\n/language – ቋንቋ ቀይር\n/cancel – አሁን ያለውን ድርጊት ሰርዝ",
        "error": "⚠️ ችግር ተፈጥሯል። እባክዎ እንደገና ይሞክሩ።",
        # ---- social media exchange ----
        "so_menu": "🌐 <b>የሶሻል ሚዲያ ልውውጥ</b>\nአካውንቶችን በአስተማማኝ መንገድ ይግዙ፣ ይሽጡ ወይም ይለውጡ — አስተዳዳሪያችን በሁለቱም ወገኖች መካከል እንደ ታማኝ አማላጅ (Escrow) ያገለግላል።\n\nምን ማድረግ ይፈልጋሉ?",
        "so_post_btn": "➕ ለጥፍ / ጠይቅ",
        "so_browse_btn": "🔍 አካውንቶችን ተመልከት",
        "so_mine_btn": "📋 የኔ ልጥፎች",
        "so_platform": "🌐 መድረኩን ይምረጡ፦",
        "so_type": "ምን ማድረግ ይፈልጋሉ?",
        "dt_sell": "💰 መሸጥ",
        "dt_swap": "🔄 መለወጥ",
        "dt_buy": "🛒 መግዛት እፈልጋለሁ",
        "so_handle": "🔗 የአካውንቱን ስም ወይም ሊንክ ያስገቡ (አስተዳዳሪው ብቻ ነው የሚያየው)፦",
        "so_followers": "👥 የተከታዮች/ሰብስክራይበሮች ብዛት? (ለምሳሌ 12000 ወይም 12k)",
        "so_desc": "📄 አካውንቱን ይግለጹ (ይዘት፣ እድሜ፣ ተሳትፎ፣ ገቢ ወዘተ)፦",
        "so_desc_buy": "📄 የሚፈልጉትን ይግለጹ (ይዘት፣ ዝቅተኛ ተከታዮች ወዘተ)፦",
        "so_price": "💰 የመሸጫ ዋጋ በብር (ETB) ያስገቡ (ቁጥር ብቻ)፦",
        "so_price_buy": "💰 በጀትዎን በብር (ETB) ያስገቡ (ቁጥር ብቻ)፦",
        "so_wanted": "🔄 በምን ለመለወጥ ይፈልጋሉ? (ለምሳሌ 5k ተከታዮች ያለው Facebook ገጽ)",
        "so_submitted_pending": "✅ ተልኳል! አስተዳዳሪያችን ልጥፍዎን ይገመግማል፣ ያነጋግርዎታልም። ከተፈቀደ በኋላ ይለጠፋል።",
        "so_submitted_active": "✅ ልጥፍዎ ተለጥፏል! አስተዳዳሪው ተነግሮታል፤ እንደ Escrow ያገለግላል።",
        "so_approved": "🎉 ልጥፍ #{id} ተፈቅዷል፤ አሁን ለሁሉም ይታያል!",
        "so_rejected": "❌ ልጥፍ #{id} አልተፈቀደም። ለዝርዝር አስተዳዳሪውን ያነጋግሩ።",
        "so_none": "😕 እስካሁን እዚህ ምንም አካውንት የለም።",
        "so_btn_deal": "🤝 የዋስትና ግብይት ጀምር",
        "so_deal_msg": "✍️ ለአስተዳዳሪው (Escrow) ጥያቄዎን ይጻፉ (የሚያቀርቡት ዋጋ፣ የሚለውጡት አካውንት፣ ማስታወሻ)፦",
        "so_deal_sent": "✅ ጥያቄዎ ተቀብሏል! አስተዳዳሪያችን (Escrow) በቅርቡ እርስዎንና ሌላኛውን ወገን ያነጋግራል።\n\n⚠️ ክፍያ ወይም የአካውንት መዳረሻ በቀጥታ ለሌላ ሰው አይስጡ።",
        "so_deal_owner": "📩 በ{platform} ልጥፍዎ #{id} ላይ ፍላጎት ያለው ሰው አለ!\nአስተዳዳሪያችን (Escrow) ግብይቱን በአስተማማኝ ሁኔታ ለማመቻቸት በቅርቡ ያነጋግርዎታል።",
        "so_deal_done": "🎉 ግብይት #{id} በስኬት ተጠናቋል! the Lwe ስለመረጡ እናመሰግናለን።",
        "so_deal_cancelled": "❌ ግብይት #{id} በአስተዳዳሪው ተሰርዟል።",
        "so_deal_exists": "ℹ️ ለዚህ ልጥፍ አስቀድሞ ክፍት ግብይት አለዎት።",
        "so_unavailable": "❌ ይህ ልጥፍ አሁን የለም።",
        "so_own": "❌ ይህ የራስዎ ልጥፍ ነው።",
        "so_mine_empty": "እስካሁን ምንም የሶሻል ሚዲያ ልጥፍ የለዎትም።",
        "so_st_pending": "⏳ ግምገማ በመጠበቅ ላይ",
        "so_st_active": "🟢 ንቁ",
        "so_st_rejected": "🔴 ተቀባይነት አላገኘም",
        "so_st_sold": "✅ ተጠናቋል",
        "so_delete": "🗑 ሰርዝ",
        "so_deleted": "🗑 ልጥፉ ተሰርዟል።",
        "so_hidden": "🔒 የአካውንቱ ዝርዝር በግብይቱ ወቅት በአስተዳዳሪው በአስተማማኝ ሁኔታ ይጋራል።",
        "so_choose_platform": "🔍 ለማየት መድረክ ይምረጡ፦",
        "lbl_platform": "መድረክ",
        "lbl_followers": "ተከታዮች",
        "lbl_price": "ዋጋ",
        "lbl_budget": "በጀት",
    },
}

MENU_KEYS = ["m_post", "m_browse", "m_search", "m_social", "m_items", "m_alerts", "m_profile", "m_lang"]
ACTION_BY_LABEL = {T[lg][k]: k for lg in T for k in MENU_KEYS}
CANCEL_LABELS = {T[lg]["cancel"] for lg in T}


# ----------------------------------------------------------------------------
# Database layer: PostgreSQL / Supabase (all queries parameterized with %s)
# ----------------------------------------------------------------------------
_pool = None
_pool_lock = threading.Lock()


def get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            kwargs = {} if "sslmode=" in DATABASE_URL else {"sslmode": "require"}
            _pool = pool.ThreadedConnectionPool(1, 10, dsn=DATABASE_URL, connect_timeout=15, **kwargs)
            logger.info("PostgreSQL connection pool created")
        return _pool


def db(sql, params=(), fetch=None):
    """fetch: 'one' | 'all' | 'id' (INSERT ... RETURNING id) | None."""
    last_err = None
    for attempt in (1, 2):  # one automatic retry if the connection was dropped
        p = get_pool()
        conn = p.getconn()
        broken = False
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if fetch == "id":
                    cur.execute(sql + " RETURNING id", params)
                    row = cur.fetchone()
                    result = row["id"] if row else None
                else:
                    cur.execute(sql, params)
                    if fetch == "one":
                        result = cur.fetchone()
                    elif fetch == "all":
                        result = cur.fetchall()
                    else:
                        result = None
            conn.commit()
            return result
        except (OperationalError, InterfaceError) as e:
            broken = True
            last_err = e
            logger.warning("DB connection problem (attempt %s): %s", attempt, e)
            try:
                conn.rollback()
            except Exception:
                pass
        except PgError:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.exception("DB error | sql=%s", sql)
            raise
        finally:
            try:
                p.putconn(conn, close=broken)
            except Exception:
                pass
    raise last_err


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    BIGINT PRIMARY KEY,
    full_name  TEXT NOT NULL,
    phone      TEXT NOT NULL,
    location   TEXT NOT NULL,
    username   TEXT,
    lang       TEXT NOT NULL DEFAULT 'en',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS items (
    id             BIGSERIAL PRIMARY KEY,
    owner_id       BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    title          TEXT NOT NULL,
    description    TEXT NOT NULL,
    category       TEXT NOT NULL,
    item_condition TEXT NOT NULL,
    photos         TEXT DEFAULT '',
    est_value      DOUBLE PRECISION DEFAULT 0,
    wanted_item    TEXT DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',
    created_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS swap_offers (
    id              BIGSERIAL PRIMARY KEY,
    item_id         BIGINT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    offerer_id      BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    offered_item_id BIGINT REFERENCES items(id) ON DELETE SET NULL,
    message         TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS wishlist (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    keyword    TEXT,
    category   TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS social_listings (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    platform    TEXT NOT NULL,
    deal_type   TEXT NOT NULL,
    handle      TEXT DEFAULT '-',
    followers   TEXT DEFAULT '-',
    description TEXT DEFAULT '',
    price       DOUBLE PRECISION DEFAULT 0,
    wanted      TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS social_deals (
    id         BIGSERIAL PRIMARY KEY,
    listing_id BIGINT NOT NULL REFERENCES social_listings(id) ON DELETE CASCADE,
    buyer_id   BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    message    TEXT DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status, category);
CREATE INDEX IF NOT EXISTS idx_wishlist_user ON wishlist(user_id);
CREATE INDEX IF NOT EXISTS idx_social_status ON social_listings(status, platform);
-- Supabase: block public API access to personal data (the bot connects as owner, so it is unaffected)
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE items ENABLE ROW LEVEL SECURITY;
ALTER TABLE swap_offers ENABLE ROW LEVEL SECURITY;
ALTER TABLE wishlist ENABLE ROW LEVEL SECURITY;
ALTER TABLE social_listings ENABLE ROW LEVEL SECURITY;
ALTER TABLE social_deals ENABLE ROW LEVEL SECURITY;
"""


def init_db():
    p = get_pool()
    conn = p.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
        conn.commit()
        logger.info("Database schema ready")
    except Exception:
        conn.rollback()
        logger.exception("Could not initialise the database")
        raise
    finally:
        p.putconn(conn)


# --- users ---
def get_user(uid):
    return db("SELECT * FROM users WHERE user_id = %s", (uid,), "one")


def save_user(uid, name, phone, location, username, lang):
    db(
        """INSERT INTO users (user_id, full_name, phone, location, username, lang)
           VALUES (%s,%s,%s,%s,%s,%s)
           ON CONFLICT (user_id) DO UPDATE SET full_name=EXCLUDED.full_name, phone=EXCLUDED.phone,
           location=EXCLUDED.location, username=EXCLUDED.username, lang=EXCLUDED.lang""",
        (uid, name, phone, location, username, lang),
    )
    lang_cache[uid] = lang


def update_username(uid, username):
    db("UPDATE users SET username = %s WHERE user_id = %s", (username, uid))


def set_lang(uid, lang):
    db("UPDATE users SET lang = %s WHERE user_id = %s", (lang, uid))
    lang_cache[uid] = lang


def lang_of(uid):
    if uid in lang_cache:
        return lang_cache[uid]
    u = get_user(uid)
    if u:
        lang_cache[uid] = u["lang"]
        return u["lang"]
    return states.get(uid, {}).get("data", {}).get("lang", "en")


def tr(uid, key, **kw):
    text = T[lang_of(uid)][key]
    return text.format(**kw) if kw else text


# --- items ---
def add_item(owner_id, d):
    return db(
        """INSERT INTO items (owner_id,title,description,category,item_condition,photos,est_value,wanted_item)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (owner_id, d["title"], d["description"], d["category"], d["condition"],
         ",".join(d.get("photos", [])), d["value"], d["wanted"]),
        "id",
    )


def get_item(item_id):
    return db("SELECT * FROM items WHERE id = %s", (item_id,), "one")


def user_items(uid):
    return db("SELECT * FROM items WHERE owner_id = %s ORDER BY id DESC LIMIT 15", (uid,), "all")


def delete_item(item_id, owner_id):
    db("DELETE FROM items WHERE id = %s AND owner_id = %s", (item_id, owner_id))


def mark_swapped(item_id, owner_id):
    db("UPDATE items SET status='swapped' WHERE id = %s AND owner_id = %s", (item_id, owner_id))


EDITABLE = {"title": "title", "description": "description", "est_value": "est_value", "wanted_item": "wanted_item"}


def update_item_field(item_id, owner_id, field, value):
    col = EDITABLE[field]  # whitelist -> safe to place in SQL text
    db(f"UPDATE items SET {col} = %s WHERE id = %s AND owner_id = %s", (value, item_id, owner_id))


def browse_ids(category):
    if category == "all":
        rows = db("SELECT id FROM items WHERE status='active' ORDER BY id DESC LIMIT 200", (), "all")
    else:
        rows = db("SELECT id FROM items WHERE status='active' AND category = %s ORDER BY id DESC LIMIT 200",
                  (category,), "all")
    return [r["id"] for r in rows]


def search_ids(q):
    rows = db(
        """SELECT id FROM items WHERE status='active' AND
           (POSITION(lower(%s) IN lower(title)) > 0 OR POSITION(lower(%s) IN lower(description)) > 0)
           ORDER BY id DESC LIMIT 200""",
        (q, q), "all",
    )
    return [r["id"] for r in rows]


# --- wishlist ---
def add_wishlist(uid, keyword=None, category=None):
    """Returns True if inserted, False if duplicate."""
    keyword = keyword.strip() if keyword else None
    exists = db(
        """SELECT id FROM wishlist WHERE user_id = %s
           AND COALESCE(keyword,'') = COALESCE(%s,'') AND COALESCE(category,'') = COALESCE(%s,'')""",
        (uid, keyword, category), "one",
    )
    if exists:
        return False
    db("INSERT INTO wishlist (user_id, keyword, category) VALUES (%s,%s,%s)", (uid, keyword, category))
    return True


def user_wishlist(uid):
    return db("SELECT * FROM wishlist WHERE user_id = %s ORDER BY id DESC", (uid,), "all")


def delete_wishlist(wid, uid):
    db("DELETE FROM wishlist WHERE id = %s AND user_id = %s", (wid, uid))


def wishlist_matches(item):
    """User ids whose wishlist matches the new item's title/description/category."""
    cat = CATEGORIES.get(item["category"], ("", ""))
    haystack = " ".join([item["title"], item["description"], cat[0], cat[1]])
    rows = db(
        """SELECT DISTINCT user_id FROM wishlist
           WHERE user_id != %s AND (
                 (category IS NOT NULL AND category = %s)
              OR (keyword IS NOT NULL AND keyword != '' AND POSITION(lower(keyword) IN lower(%s)) > 0)
           )""",
        (item["owner_id"], item["category"], haystack), "all",
    )
    return [r["user_id"] for r in rows]


# --- item swap offers ---
def create_offer(item_id, offerer_id, offered_item_id, message):
    return db(
        "INSERT INTO swap_offers (item_id, offerer_id, offered_item_id, message) VALUES (%s,%s,%s,%s)",
        (item_id, offerer_id, offered_item_id, message), "id",
    )


def get_offer(offer_id):
    return db("SELECT * FROM swap_offers WHERE id = %s", (offer_id,), "one")


def set_offer_status(offer_id, status):
    db("UPDATE swap_offers SET status = %s WHERE id = %s", (status, offer_id))


# --- social listings & deals ---
def add_listing(owner_id, d, status):
    return db(
        """INSERT INTO social_listings (owner_id,platform,deal_type,handle,followers,description,price,wanted,status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (owner_id, d["platform"], d["deal_type"], d["handle"], d["followers"],
         d["description"], d["price"], d["wanted"], status), "id",
    )


def get_listing(lid):
    return db("SELECT * FROM social_listings WHERE id = %s", (lid,), "one")


def user_listings(uid):
    return db("SELECT * FROM social_listings WHERE owner_id = %s ORDER BY id DESC LIMIT 15", (uid,), "all")


def set_listing_status(lid, status):
    db("UPDATE social_listings SET status = %s WHERE id = %s", (status, lid))


def delete_listing(lid, owner_id):
    db("DELETE FROM social_listings WHERE id = %s AND owner_id = %s", (lid, owner_id))


def social_ids(platform):
    if platform == "all":
        rows = db("SELECT id FROM social_listings WHERE status='active' ORDER BY id DESC LIMIT 200", (), "all")
    else:
        rows = db("SELECT id FROM social_listings WHERE status='active' AND platform = %s ORDER BY id DESC LIMIT 200",
                  (platform,), "all")
    return [r["id"] for r in rows]


def create_deal(listing_id, buyer_id, message):
    return db("INSERT INTO social_deals (listing_id, buyer_id, message) VALUES (%s,%s,%s)",
              (listing_id, buyer_id, message), "id")


def get_deal(did):
    return db("SELECT * FROM social_deals WHERE id = %s", (did,), "one")


def set_deal_status(did, status):
    db("UPDATE social_deals SET status = %s WHERE id = %s", (status, did))


def open_deal_exists(listing_id, buyer_id):
    return db("SELECT id FROM social_deals WHERE listing_id = %s AND buyer_id = %s AND status = 'open'",
              (listing_id, buyer_id), "one") is not None


# ----------------------------------------------------------------------------
# Keyboards
# ----------------------------------------------------------------------------
def main_menu(uid):
    lang = lang_of(uid)
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(*[types.KeyboardButton(T[lang][k]) for k in MENU_KEYS])
    return kb


def cancel_kb(uid):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton(tr(uid, "cancel")))
    return kb


def phone_kb(uid):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton(tr(uid, "share_phone"), request_contact=True))
    kb.add(types.KeyboardButton(tr(uid, "cancel")))
    return kb


def lang_kb():
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("English 🇬🇧", callback_data="lang:en"),
           types.InlineKeyboardButton("አማርኛ 🇪🇹", callback_data="lang:am"))
    return kb


def join_kb():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(JOIN_BTN, url=CHANNEL_URL))
    kb.add(types.InlineKeyboardButton(CONTINUE_BTN, callback_data="join:ok"))
    return kb


def category_kb(uid, prefix, with_all=False):
    lang = lang_of(uid)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(*[types.InlineKeyboardButton(cat_name(k, lang), callback_data=f"{prefix}:{k}") for k in CATEGORIES])
    if with_all:
        kb.add(types.InlineKeyboardButton(T[lang]["cat_all"], callback_data=f"{prefix}:all"))
    return kb


def platform_kb(uid, prefix, with_all=False):
    lang = lang_of(uid)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(*[types.InlineKeyboardButton(plat_name(k, lang), callback_data=f"{prefix}:{k}") for k in PLATFORMS])
    if with_all:
        kb.add(types.InlineKeyboardButton(T[lang]["cat_all"], callback_data=f"{prefix}:all"))
    return kb


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def normalize_phone(raw):
    s = re.sub(r"[\s\-()]", "", raw or "")
    m = re.fullmatch(r"(?:\+251|251|0)?([79]\d{8})", s)
    return "+251" + m.group(1) if m else None


def parse_number(text):
    try:
        value = float(re.sub(r"(ETB|ብር|,)", "", text or "").strip())
        return value if value >= 0 else None
    except ValueError:
        return None


def is_channel_member(uid):
    """Checks channel membership. The bot must be an ADMIN of the channel for this to work.
    If the check itself fails (bot not admin, wrong channel), we let the user through."""
    try:
        member = bot.get_chat_member(CHANNEL_USERNAME, uid)
        return member.status in ("member", "administrator", "creator", "restricted")
    except Exception as e:
        logger.warning("Channel check failed (add the bot as channel admin): %s", e)
        return True


def item_caption(item, lang, header=""):
    owner = get_user(item["owner_id"])
    loc = owner["location"] if owner else "-"
    cond = T[lang]["cond_new" if item["item_condition"] == "new" else "cond_used"]
    desc = item["description"]
    if len(desc) > 400:
        desc = desc[:400] + "…"
    status = ("\n" + T[lang]["st_swapped"]) if item["status"] != "active" else ""
    value = item["est_value"]
    value_txt = f"{value:,.0f} ETB" if value is not None else "-"
    return (
        f"{header}<b>{esc(item['title'])}</b>\n\n{esc(desc)}\n\n"
        f"🗂 {T[lang]['lbl_cat']}: {esc(cat_name(item['category'], lang))}\n"
        f"⚙️ {T[lang]['lbl_cond']}: {cond}\n"
        f"💰 {T[lang]['lbl_value']}: {value_txt}\n"
        f"🔄 {T[lang]['lbl_wanted']}: {esc(item['wanted_item'])}\n"
        f"📍 {T[lang]['lbl_loc']}: {esc(loc)}{status}"
    )[:1020]


def send_item(chat_id, item, lang, markup=None, header=""):
    caption = item_caption(item, lang, header)
    photos = [p for p in (item["photos"] or "").split(",") if p]
    if photos:
        try:
            return bot.send_photo(chat_id, photos[0], caption=caption, reply_markup=markup)
        except Exception:
            logger.warning("send_photo failed for item %s, falling back to text", item["id"])
    return bot.send_message(chat_id, caption, reply_markup=markup)


def social_caption(l, lang, header="", show_status=False):
    L = T[lang]
    owner = get_user(l["owner_id"])
    loc = owner["location"] if owner else "-"
    lines = [f"{header}<b>#{l['id']} · {esc(plat_name(l['platform'], lang))}</b>  {L['dt_' + l['deal_type']]}", ""]
    if l["followers"] and l["followers"] != "-":
        lines.append(f"👥 {L['lbl_followers']}: {esc(l['followers'])}")
    if l["description"]:
        desc = l["description"][:400] + ("…" if len(l["description"]) > 400 else "")
        lines.append(f"📄 {esc(desc)}")
    if l["deal_type"] == "sell":
        lines.append(f"💰 {L['lbl_price']}: {l['price']:,.0f} ETB")
    elif l["deal_type"] == "swap":
        lines.append(f"🔄 {L['lbl_wanted']}: {esc(l['wanted'])}")
    else:
        lines.append(f"💰 {L['lbl_budget']}: {l['price']:,.0f} ETB")
    lines.append(f"📍 {L['lbl_loc']}: {esc(loc)}")
    if l["deal_type"] != "buy":
        lines.append(L["so_hidden"])
    if show_status:
        lines.append("")
        lines.append(L["so_st_" + l["status"]])
    return "\n".join(lines)[:4000]


def clear_state(uid):
    states.pop(uid, None)


def show_menu(uid, chat_id, text=None):
    bot.send_message(chat_id, text or tr(uid, "menu_title"), reply_markup=main_menu(uid))


# ----------------------------------------------------------------------------
# Admin escrow notifications (admin messages are in English)
# ----------------------------------------------------------------------------
def contact_block(u, role):
    if not u:
        return f"<b>{role}</b>\n(user not found)"
    uname = f"@{esc(u['username'])}" if u.get("username") else "—"
    return (
        f"<b>{role}</b>\n"
        f"👤 <a href=\"tg://user?id={u['user_id']}\">{esc(u['full_name'])}</a>\n"
        f"🔗 {uname}\n"
        f"📞 {esc(u['phone'])}\n"
        f"📍 {esc(u['location'])}\n"
        f"🆔 <code>{u['user_id']}</code>"
    )


def listing_admin_details(l):
    kind = {"sell": "💰 SELL", "swap": "🔄 SWAP", "buy": "🛒 WANT TO BUY"}[l["deal_type"]]
    extra = ""
    if l["deal_type"] == "sell":
        extra = f"💰 Asking price: {l['price']:,.0f} ETB\n"
    elif l["deal_type"] == "swap":
        extra = f"🔄 Wants in exchange: {esc(l['wanted'])}\n"
    else:
        extra = f"💰 Budget: {l['price']:,.0f} ETB\n"
    return (
        f"Type: {kind}\n"
        f"🌐 Platform: {esc(plat_name(l['platform'], 'en'))}\n"
        f"🔗 Account: {esc(l['handle'])}\n"
        f"👥 Followers: {esc(l['followers'])}\n"
        f"📄 Description: {esc(l['description'])}\n"
        f"{extra}"
    )


def send_listing_to_admin(listing_id):
    try:
        l = get_listing(listing_id)
        if not l:
            return
        owner = get_user(l["owner_id"])
        text = (
            f"🆕 <b>Social media listing #{l['id']}</b>  [{l['status'].upper()}]\n\n"
            f"{listing_admin_details(l)}\n{contact_block(owner, 'OWNER')}"
        )
        kb = None
        if l["status"] == "pending":
            kb = types.InlineKeyboardMarkup()
            kb.row(types.InlineKeyboardButton("✅ Approve", callback_data=f"sla:{l['id']}"),
                   types.InlineKeyboardButton("❌ Reject", callback_data=f"slr:{l['id']}"))
        bot.send_message(ADMIN_ID, text, reply_markup=kb)
    except Exception:
        logger.exception("Could not notify admin about listing %s (has the admin started the bot?)", listing_id)


def send_deal_to_admin(deal_id):
    try:
        d = get_deal(deal_id)
        if not d:
            return
        l = get_listing(d["listing_id"])
        buyer = get_user(d["buyer_id"])
        owner = get_user(l["owner_id"])
        text = (
            f"🤝 <b>NEW ESCROW DEAL #{d['id']}</b>\n"
            f"Listing #{l['id']}\n\n"
            f"{listing_admin_details(l)}\n"
            f"💬 <b>Requester's message:</b>\n{esc(d['message'])}\n\n"
            f"{contact_block(owner, 'LISTING OWNER')}\n\n"
            f"{contact_block(buyer, 'REQUESTER')}\n\n"
            f"Contact both parties and hold the deal until it is safe to complete."
        )
        kb = types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton("✅ Deal completed", callback_data=f"sdc:{d['id']}"),
               types.InlineKeyboardButton("❌ Cancel deal", callback_data=f"sdx:{d['id']}"))
        bot.send_message(ADMIN_ID, text, reply_markup=kb)
    except Exception:
        logger.exception("Could not notify admin about deal %s (has the admin started the bot?)", deal_id)


def notify_listing_owner(deal_id):
    try:
        d = get_deal(deal_id)
        l = get_listing(d["listing_id"])
        lang = lang_of(l["owner_id"])
        bot.send_message(l["owner_id"], T[lang]["so_deal_owner"].format(
            platform=plat_name(l["platform"], lang), id=l["id"]))
    except Exception:
        logger.warning("Could not notify listing owner for deal %s", deal_id)


# ----------------------------------------------------------------------------
# Wishlist notification broadcaster
# ----------------------------------------------------------------------------
def notify_wishlist(item_id):
    try:
        item = get_item(item_id)
        if not item:
            return
        for target in wishlist_matches(item):
            try:
                lang = lang_of(target)
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton(T[lang]["btn_offer"], callback_data=f"of:{item_id}"))
                send_item(target, item, lang, kb, header=T[lang]["n_match"])
            except Exception:
                logger.warning("Could not notify user %s (blocked the bot?)", target)
    except Exception:
        logger.exception("notify_wishlist failed")


# ----------------------------------------------------------------------------
# Browse / pagination (items + social listings)
# ----------------------------------------------------------------------------
def show_list(chat_id, uid, idx):
    lang = lang_of(uid)
    ctx = list_ctx.get(uid)
    if not ctx or not ctx["ids"]:
        bot.send_message(chat_id, T[lang]["expired"])
        return
    ids = ctx["ids"]
    idx = max(0, min(idx, len(ids) - 1))
    item = get_item(ids[idx])
    if not item:
        ids.pop(idx)
        return show_list(chat_id, uid, idx)

    kb = types.InlineKeyboardMarkup()
    prev_b = types.InlineKeyboardButton("◀️", callback_data=f"pg:{idx - 1}") if idx > 0 \
        else types.InlineKeyboardButton("·", callback_data="noop")
    next_b = types.InlineKeyboardButton("▶️", callback_data=f"pg:{idx + 1}") if idx < len(ids) - 1 \
        else types.InlineKeyboardButton("·", callback_data="noop")
    kb.row(prev_b, types.InlineKeyboardButton(f"{idx + 1}/{len(ids)}", callback_data="noop"), next_b)
    if item["owner_id"] != uid and item["status"] == "active":
        kb.add(types.InlineKeyboardButton(T[lang]["btn_offer"], callback_data=f"of:{item['id']}"))
    if len([p for p in (item["photos"] or "").split(",") if p]) > 1:
        kb.add(types.InlineKeyboardButton(T[lang]["btn_photos"], callback_data=f"ph:{item['id']}"))
    send_item(chat_id, item, lang, kb)


def show_social_list(chat_id, uid, idx):
    lang = lang_of(uid)
    ctx = social_ctx.get(uid)
    if not ctx or not ctx["ids"]:
        bot.send_message(chat_id, T[lang]["expired"])
        return
    ids = ctx["ids"]
    idx = max(0, min(idx, len(ids) - 1))
    l = get_listing(ids[idx])
    if not l or l["status"] != "active":
        ids.pop(idx)
        return show_social_list(chat_id, uid, idx)

    kb = types.InlineKeyboardMarkup()
    prev_b = types.InlineKeyboardButton("◀️", callback_data=f"sp:{idx - 1}") if idx > 0 \
        else types.InlineKeyboardButton("·", callback_data="noop")
    next_b = types.InlineKeyboardButton("▶️", callback_data=f"sp:{idx + 1}") if idx < len(ids) - 1 \
        else types.InlineKeyboardButton("·", callback_data="noop")
    kb.row(prev_b, types.InlineKeyboardButton(f"{idx + 1}/{len(ids)}", callback_data="noop"), next_b)
    if l["owner_id"] != uid:
        kb.add(types.InlineKeyboardButton(T[lang]["so_btn_deal"], callback_data=f"sd:{l['id']}"))
    bot.send_message(chat_id, social_caption(l, lang), reply_markup=kb)


# ----------------------------------------------------------------------------
# Menu actions
# ----------------------------------------------------------------------------
def action_post(uid, chat_id):
    states[uid] = {"step": "p_title", "data": {"photos": []}}
    bot.send_message(chat_id, tr(uid, "p_title"), reply_markup=cancel_kb(uid))


def action_browse(uid, chat_id):
    bot.send_message(chat_id, tr(uid, "b_choose_cat"), reply_markup=category_kb(uid, "bc", with_all=True))


def action_search(uid, chat_id):
    states[uid] = {"step": "search", "data": {}}
    bot.send_message(chat_id, tr(uid, "s_ask"), reply_markup=cancel_kb(uid))


def action_social(uid, chat_id):
    lang = lang_of(uid)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(T[lang]["so_post_btn"], callback_data="soc:post"))
    kb.add(types.InlineKeyboardButton(T[lang]["so_browse_btn"], callback_data="soc:browse"))
    kb.add(types.InlineKeyboardButton(T[lang]["so_mine_btn"], callback_data="soc:mine"))
    bot.send_message(chat_id, T[lang]["so_menu"], reply_markup=kb)


def action_items(uid, chat_id):
    lang = lang_of(uid)
    items = user_items(uid)
    if not items:
        bot.send_message(chat_id, T[lang]["mi_empty"])
        return
    for it in items:
        kb = types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton(T[lang]["btn_edit"], callback_data=f"mi:ed:{it['id']}"),
               types.InlineKeyboardButton(T[lang]["btn_delete"], callback_data=f"mi:dl:{it['id']}"))
        if it["status"] == "active":
            kb.add(types.InlineKeyboardButton(T[lang]["btn_swapped"], callback_data=f"mi:sw:{it['id']}"))
        send_item(chat_id, it, lang, kb)


def action_alerts(uid, chat_id):
    lang = lang_of(uid)
    rows = user_wishlist(uid)
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        label = f"🔤 {r['keyword']}" if r["keyword"] else f"🗂 {cat_name(r['category'], lang)}"
        kb.add(types.InlineKeyboardButton(f"🗑 {label}"[:60], callback_data=f"ald:{r['id']}"))
    kb.add(types.InlineKeyboardButton(T[lang]["a_add_btn"], callback_data="al:kw"))
    bot.send_message(chat_id, T[lang]["a_list_title"] if rows else T[lang]["a_empty"], reply_markup=kb)


def action_profile(uid, chat_id):
    u = get_user(uid)
    bot.send_message(chat_id, tr(uid, "pf_text", name=esc(u["full_name"]), phone=esc(u["phone"]),
                                 loc=esc(u["location"])))


def action_lang(uid, chat_id):
    new = "am" if lang_of(uid) == "en" else "en"
    set_lang(uid, new)
    show_menu(uid, chat_id, T[new]["lang_changed"])


ACTIONS = {
    "m_post": action_post, "m_browse": action_browse, "m_search": action_search,
    "m_social": action_social, "m_items": action_items, "m_alerts": action_alerts,
    "m_profile": action_profile, "m_lang": action_lang,
}


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------
def prompt_language(chat_id):
    bot.send_message(chat_id, CHOOSE_LANG, reply_markup=lang_kb())


@bot.message_handler(commands=["start", "menu"])
def cmd_start(m):
    uid = m.from_user.id
    clear_state(uid)
    if get_user(uid):
        show_menu(uid, m.chat.id)
    else:
        prompt_language(m.chat.id)


@bot.message_handler(commands=["cancel"])
def cmd_cancel(m):
    uid = m.from_user.id
    clear_state(uid)
    if get_user(uid):
        show_menu(uid, m.chat.id, tr(uid, "cancelled"))
    else:
        prompt_language(m.chat.id)


@bot.message_handler(commands=["help"])
def cmd_help(m):
    bot.send_message(m.chat.id, tr(m.from_user.id, "help"))


@bot.message_handler(commands=["language"])
def cmd_language(m):
    prompt_language(m.chat.id)


@bot.message_handler(commands=["pending"])
def cmd_pending(m):
    """Admin only: re-send all pending listings and open escrow deals."""
    if m.from_user.id != ADMIN_ID:
        return
    try:
        listings = db("SELECT id FROM social_listings WHERE status='pending' ORDER BY id", (), "all")
        deals = db("SELECT id FROM social_deals WHERE status='open' ORDER BY id", (), "all")
        bot.send_message(m.chat.id, f"📋 Pending listings: {len(listings)} | Open deals: {len(deals)}")
        for r in listings:
            send_listing_to_admin(r["id"])
        for r in deals:
            send_deal_to_admin(r["id"])
    except Exception:
        logger.exception("/pending failed")


# ----------------------------------------------------------------------------
# Conversation state machine
# ----------------------------------------------------------------------------
@bot.message_handler(content_types=["text", "photo", "contact"])
def on_message(m):
    uid, chat_id = m.from_user.id, m.chat.id
    try:
        text = (m.text or "").strip() if m.content_type == "text" else ""
        user = get_user(uid)

        if user and (m.from_user.username or None) != user.get("username"):
            update_username(uid, m.from_user.username)

        if text in CANCEL_LABELS:
            clear_state(uid)
            if user:
                show_menu(uid, chat_id, tr(uid, "cancelled"))
            else:
                prompt_language(chat_id)
            return

        if user and text in ACTION_BY_LABEL:
            clear_state(uid)
            ACTIONS[ACTION_BY_LABEL[text]](uid, chat_id)
            return

        st = states.get(uid)
        if st:
            handle_state(m, st, text)
            return

        if user:
            show_menu(uid, chat_id)
        else:
            prompt_language(chat_id)
    except Exception:
        logger.exception("on_message error")
        clear_state(uid)
        try:
            bot.send_message(chat_id, tr(uid, "error"))
        except Exception:
            pass


SOCIAL_FLOW = {
    "sell": ["so_handle", "so_followers", "so_desc", "so_price"],
    "swap": ["so_handle", "so_followers", "so_desc", "so_wanted"],
    "buy": ["so_desc", "so_price"],
}
SOCIAL_FIELD = {"so_handle": "handle", "so_followers": "followers", "so_desc": "description", "so_wanted": "wanted"}


def social_prompt(uid, chat_id, step, deal_type):
    key = step + "_buy" if deal_type == "buy" and step in ("so_desc", "so_price") else step
    bot.send_message(chat_id, tr(uid, key), reply_markup=cancel_kb(uid))


def finish_social_post(uid, chat_id, data):
    status = "active" if SOCIAL_AUTO_APPROVE else "pending"
    listing_id = add_listing(uid, data, status)
    clear_state(uid)
    show_menu(uid, chat_id, tr(uid, "so_submitted_active" if status == "active" else "so_submitted_pending"))
    run_async(send_listing_to_admin, listing_id)  # escrow: full details + contacts go to the admin


def handle_state(m, st, text):
    uid, chat_id = m.from_user.id, m.chat.id
    step, data = st["step"], st["data"]

    # ---------- channel gate ----------
    if step == "join":
        return bot.send_message(chat_id, T[data["lang"]]["join_prompt"], reply_markup=join_kb())

    # ---------- registration ----------
    if step == "reg_name":
        if not text:
            return bot.send_message(chat_id, tr(uid, "text_needed"))
        data["name"] = text[:100]
        st["step"] = "reg_phone"
        return bot.send_message(chat_id, tr(uid, "ask_phone"), reply_markup=phone_kb(uid))

    if step == "reg_phone":
        raw = m.contact.phone_number if m.content_type == "contact" else text
        phone = normalize_phone(raw)
        if not phone:
            return bot.send_message(chat_id, tr(uid, "bad_phone"))
        data["phone"] = phone
        st["step"] = "reg_loc"
        return bot.send_message(chat_id, tr(uid, "ask_location"), reply_markup=cancel_kb(uid))

    if step == "reg_loc":
        if not text:
            return bot.send_message(chat_id, tr(uid, "text_needed"))
        save_user(uid, data["name"], data["phone"], text[:100], m.from_user.username, data.get("lang", "en"))
        clear_state(uid)
        return show_menu(uid, chat_id, tr(uid, "registered"))

    # ---------- post item ----------
    if step == "p_title":
        if not text:
            return bot.send_message(chat_id, tr(uid, "text_needed"))
        data["title"] = text[:120]
        st["step"] = "p_desc"
        return bot.send_message(chat_id, tr(uid, "p_desc"))

    if step == "p_desc":
        if not text:
            return bot.send_message(chat_id, tr(uid, "text_needed"))
        data["description"] = text[:1000]
        st["step"] = "p_cat"
        return bot.send_message(chat_id, tr(uid, "p_cat"), reply_markup=category_kb(uid, "pc"))

    if step == "p_photos":
        if m.content_type == "photo":
            if len(data["photos"]) < MAX_PHOTOS:
                data["photos"].append(m.photo[-1].file_id)
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton(tr(uid, "p_done"), callback_data="pp:done"))
            return bot.send_message(chat_id, tr(uid, "p_photo_added", n=len(data["photos"])), reply_markup=kb)
        return bot.send_message(chat_id, tr(uid, "p_need_photo"))

    if step == "p_value":
        value = parse_number(text)
        if value is None:
            return bot.send_message(chat_id, tr(uid, "bad_number"))
        data["value"] = value
        st["step"] = "p_wanted"
        return bot.send_message(chat_id, tr(uid, "p_wanted"))

    if step == "p_wanted":
        if not text:
            return bot.send_message(chat_id, tr(uid, "text_needed"))
        data["wanted"] = text[:300]
        item_id = add_item(uid, data)
        clear_state(uid)
        show_menu(uid, chat_id, tr(uid, "p_saved"))
        run_async(notify_wishlist, item_id)  # automatic wishlist matching
        return

    # ---------- search ----------
    if step == "search":
        if not text:
            return bot.send_message(chat_id, tr(uid, "text_needed"))
        clear_state(uid)
        q = text[:80]
        last_query[uid] = q
        ids = search_ids(q)
        if not ids:
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton(tr(uid, "btn_alert"), callback_data="al:q"))
            show_menu(uid, chat_id)
            return bot.send_message(chat_id, tr(uid, "s_none", q=esc(q)), reply_markup=kb)
        list_ctx[uid] = {"ids": ids}
        show_menu(uid, chat_id)
        return show_list(chat_id, uid, 0)

    # ---------- alert keyword ----------
    if step == "al_kw":
        if not text:
            return bot.send_message(chat_id, tr(uid, "text_needed"))
        clear_state(uid)
        ok = add_wishlist(uid, keyword=text[:80])
        return show_menu(uid, chat_id, tr(uid, "a_saved" if ok else "a_exists"))

    # ---------- item swap offer message ----------
    if step == "offer_msg":
        if not text:
            return bot.send_message(chat_id, tr(uid, "text_needed"))
        item = get_item(data["item_id"])
        clear_state(uid)
        if not item or item["status"] != "active":
            return show_menu(uid, chat_id, tr(uid, "o_unavailable"))
        offer_id = create_offer(item["id"], uid, data.get("my_item_id"), text[:500])
        show_menu(uid, chat_id, tr(uid, "o_sent"))
        run_async(send_offer_to_owner, offer_id)
        return

    # ---------- edit item field ----------
    if step == "edit":
        field = data["field"]
        value = text
        if field == "est_value":
            value = parse_number(text)
            if value is None:
                return bot.send_message(chat_id, tr(uid, "bad_number"))
        elif not text:
            return bot.send_message(chat_id, tr(uid, "text_needed"))
        update_item_field(data["item_id"], uid, field, value)
        clear_state(uid)
        return show_menu(uid, chat_id, tr(uid, "ed_done"))

    # ---------- social media post flow ----------
    if step in SOCIAL_FIELD or step == "so_price":
        if not text:
            return bot.send_message(chat_id, tr(uid, "text_needed"))
        if step == "so_price":
            value = parse_number(text)
            if value is None:
                return bot.send_message(chat_id, tr(uid, "bad_number"))
            data["price"] = value
        else:
            data[SOCIAL_FIELD[step]] = text[:500]
        flow = SOCIAL_FLOW[data["deal_type"]]
        i = flow.index(step)
        if i + 1 < len(flow):
            st["step"] = flow[i + 1]
            return social_prompt(uid, chat_id, st["step"], data["deal_type"])
        return finish_social_post(uid, chat_id, data)

    # ---------- escrow deal request ----------
    if step == "deal_msg":
        if not text:
            return bot.send_message(chat_id, tr(uid, "text_needed"))
        l = get_listing(data["listing_id"])
        clear_state(uid)
        if not l or l["status"] != "active":
            return show_menu(uid, chat_id, tr(uid, "so_unavailable"))
        deal_id = create_deal(l["id"], uid, text[:800])
        show_menu(uid, chat_id, tr(uid, "so_deal_sent"))
        run_async(send_deal_to_admin, deal_id)     # all details + both contacts -> admin
        run_async(notify_listing_owner, deal_id)   # owner is told the admin will contact them
        return

    clear_state(uid)
    show_menu(uid, chat_id)


def send_offer_to_owner(offer_id):
    try:
        offer = get_offer(offer_id)
        if not offer:
            return
        item = get_item(offer["item_id"])
        offerer = get_user(offer["offerer_id"])
        owner_id = item["owner_id"]
        lang = lang_of(owner_id)
        offered = T[lang]["o_no_item"]
        if offer["offered_item_id"]:
            oi = get_item(offer["offered_item_id"])
            if oi:
                offered = f"<b>{esc(oi['title'])}</b> (~{oi['est_value']:,.0f} ETB)"
        kb = types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton(T[lang]["o_accept"], callback_data=f"oa:{offer_id}"),
               types.InlineKeyboardButton(T[lang]["o_decline"], callback_data=f"od:{offer_id}"))
        bot.send_message(
            owner_id,
            T[lang]["o_received"].format(
                name=esc(offerer["full_name"]), loc=esc(offerer["location"]),
                item=esc(item["title"]), offered=offered, msg=esc(offer["message"])),
            reply_markup=kb,
        )
    except Exception:
        logger.warning("Could not deliver offer %s to owner", offer_id)


# ----------------------------------------------------------------------------
# Admin callbacks (escrow actions)
# ----------------------------------------------------------------------------
def handle_admin_callback(c, data):
    chat_id = c.message.chat.id
    try:
        bot.edit_message_reply_markup(chat_id, c.message.message_id, reply_markup=None)
    except Exception:
        pass

    if data.startswith(("sla:", "slr:")):
        lid = int(data.split(":")[1])
        l = get_listing(lid)
        if not l:
            return bot.send_message(chat_id, f"Listing #{lid} no longer exists.")
        approve = data.startswith("sla:")
        set_listing_status(lid, "active" if approve else "rejected")
        bot.send_message(chat_id, f"{'✅ Approved' if approve else '❌ Rejected'} listing #{lid}.")
        try:
            lang = lang_of(l["owner_id"])
            bot.send_message(l["owner_id"], T[lang]["so_approved" if approve else "so_rejected"].format(id=lid))
        except Exception:
            logger.warning("Could not notify owner of listing %s", lid)
        return

    if data.startswith(("sdc:", "sdx:")):
        did = int(data.split(":")[1])
        d = get_deal(did)
        if not d or d["status"] != "open":
            return bot.send_message(chat_id, f"Deal #{did} was already handled or no longer exists.")
        l = get_listing(d["listing_id"])
        done = data.startswith("sdc:")
        set_deal_status(did, "completed" if done else "cancelled")
        if done:
            set_listing_status(l["id"], "sold")
        bot.send_message(chat_id, f"{'✅ Completed' if done else '❌ Cancelled'} deal #{did}.")
        for party in (d["buyer_id"], l["owner_id"]):
            try:
                lang = lang_of(party)
                bot.send_message(party, T[lang]["so_deal_done" if done else "so_deal_cancelled"].format(id=did))
            except Exception:
                logger.warning("Could not notify user %s about deal %s", party, did)


# ----------------------------------------------------------------------------
# Callback queries
# ----------------------------------------------------------------------------
@bot.callback_query_handler(func=lambda c: True)
def on_callback(c):
    uid, chat_id = c.from_user.id, c.message.chat.id
    data = c.data or ""
    try:
        if data != "join:ok":  # join:ok answers the query itself (may show an alert)
            bot.answer_callback_query(c.id)

        if data == "noop":
            return

        # ----- admin-only escrow actions -----
        if data.startswith(("sla:", "slr:", "sdc:", "sdx:")):
            if uid == ADMIN_ID:
                handle_admin_callback(c, data)
            return

        # ----- language (registration or switch) -----
        if data.startswith("lang:"):
            lang = data.split(":")[1]
            if lang not in T:
                return
            if get_user(uid):
                set_lang(uid, lang)
                show_menu(uid, chat_id, T[lang]["lang_changed"])
            else:
                states[uid] = {"step": "join", "data": {"lang": lang}}
                bot.send_message(chat_id, T[lang]["welcome_brand"])
                bot.send_message(chat_id, T[lang]["join_prompt"], reply_markup=join_kb())
            return

        # ----- mandatory channel join -----
        if data == "join:ok":
            st = states.get(uid)
            if not st or st["step"] != "join":
                bot.answer_callback_query(c.id)
                if not get_user(uid):
                    prompt_language(chat_id)
                return
            lang = st["data"]["lang"]
            if not is_channel_member(uid):
                bot.answer_callback_query(c.id, T[lang]["join_required"], show_alert=True)
                return
            bot.answer_callback_query(c.id)
            st["step"] = "reg_name"
            bot.send_message(chat_id, T[lang]["join_ok"])
            bot.send_message(chat_id, T[lang]["ask_name"], reply_markup=cancel_kb(uid))
            return

        if not get_user(uid):
            prompt_language(chat_id)
            return
        lang = lang_of(uid)

        # ----- post item flow -----
        if data.startswith("pc:"):
            st = states.get(uid)
            cat = data.split(":")[1]
            if not st or st["step"] != "p_cat" or cat not in CATEGORIES:
                return
            st["data"]["category"] = cat
            st["step"] = "p_cond"
            kb = types.InlineKeyboardMarkup()
            kb.row(types.InlineKeyboardButton(T[lang]["cond_new"], callback_data="pn:new"),
                   types.InlineKeyboardButton(T[lang]["cond_used"], callback_data="pn:used"))
            bot.send_message(chat_id, T[lang]["p_cond"], reply_markup=kb)
            return

        if data.startswith("pn:"):
            st = states.get(uid)
            if not st or st["step"] != "p_cond":
                return
            st["data"]["condition"] = "new" if data.endswith("new") else "used"
            st["step"] = "p_photos"
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton(T[lang]["p_skip"], callback_data="pp:done"))
            bot.send_message(chat_id, T[lang]["p_photos"], reply_markup=kb)
            return

        if data == "pp:done":
            st = states.get(uid)
            if not st or st["step"] != "p_photos":
                return
            st["step"] = "p_value"
            bot.send_message(chat_id, T[lang]["p_value"])
            return

        # ----- browse items -----
        if data.startswith("bc:"):
            cat = data.split(":")[1]
            if cat != "all" and cat not in CATEGORIES:
                return
            ids = browse_ids(cat)
            if not ids:
                kb = types.InlineKeyboardMarkup()
                if cat != "all":
                    kb.add(types.InlineKeyboardButton(T[lang]["btn_alert"], callback_data=f"al:c:{cat}"))
                bot.send_message(chat_id, T[lang]["b_none"], reply_markup=kb)
                return
            list_ctx[uid] = {"ids": ids}
            show_list(chat_id, uid, 0)
            return

        if data.startswith("pg:"):
            idx = int(data.split(":")[1])
            try:
                bot.delete_message(chat_id, c.message.message_id)
            except Exception:
                pass
            show_list(chat_id, uid, idx)
            return

        if data.startswith("ph:"):
            item = get_item(int(data.split(":")[1]))
            if not item:
                return
            photos = [p for p in (item["photos"] or "").split(",") if p][:MAX_PHOTOS]
            if len(photos) > 1:
                bot.send_media_group(chat_id, [types.InputMediaPhoto(p) for p in photos])
            return

        # ----- alerts -----
        if data == "al:kw":
            states[uid] = {"step": "al_kw", "data": {}}
            bot.send_message(chat_id, T[lang]["a_ask_kw"], reply_markup=cancel_kb(uid))
            return

        if data == "al:q":
            q = last_query.get(uid)
            if not q:
                return
            ok = add_wishlist(uid, keyword=q)
            bot.send_message(chat_id, T[lang]["a_saved" if ok else "a_exists"])
            return

        if data.startswith("al:c:"):
            cat = data.split(":")[2]
            if cat in CATEGORIES:
                ok = add_wishlist(uid, category=cat)
                bot.send_message(chat_id, T[lang]["a_saved" if ok else "a_exists"])
            return

        if data.startswith("ald:"):
            delete_wishlist(int(data.split(":")[1]), uid)
            bot.send_message(chat_id, T[lang]["a_deleted"])
            return

        # ----- item swap offers -----
        if data.startswith("of:"):
            item = get_item(int(data.split(":")[1]))
            if not item or item["status"] != "active":
                bot.send_message(chat_id, T[lang]["o_unavailable"])
                return
            if item["owner_id"] == uid:
                bot.send_message(chat_id, T[lang]["o_own"])
                return
            kb = types.InlineKeyboardMarkup()
            for mine in user_items(uid):
                if mine["status"] == "active":
                    kb.add(types.InlineKeyboardButton(f"🎁 {mine['title']}"[:60],
                                                      callback_data=f"ofp:{item['id']}:{mine['id']}"))
            kb.add(types.InlineKeyboardButton(T[lang]["o_none_item"], callback_data=f"ofp:{item['id']}:0"))
            bot.send_message(chat_id, T[lang]["o_pick"], reply_markup=kb)
            return

        if data.startswith("ofp:"):
            _, item_id, my_id = data.split(":")
            item = get_item(int(item_id))
            if not item or item["status"] != "active" or item["owner_id"] == uid:
                bot.send_message(chat_id, T[lang]["o_unavailable"])
                return
            my_item = None
            if int(my_id):
                my_item = get_item(int(my_id))
                if not my_item or my_item["owner_id"] != uid:
                    return
            states[uid] = {"step": "offer_msg",
                           "data": {"item_id": item["id"], "my_item_id": my_item["id"] if my_item else None}}
            bot.send_message(chat_id, T[lang]["o_msg"], reply_markup=cancel_kb(uid))
            return

        if data.startswith(("oa:", "od:")):
            offer = get_offer(int(data.split(":")[1]))
            if not offer:
                return
            item = get_item(offer["item_id"])
            if not item or item["owner_id"] != uid:
                return
            if offer["status"] != "pending":
                bot.send_message(chat_id, T[lang]["o_already"])
                return
            offerer = get_user(offer["offerer_id"])
            owner = get_user(uid)
            try:
                bot.edit_message_reply_markup(chat_id, c.message.message_id, reply_markup=None)
            except Exception:
                pass
            if data.startswith("oa:"):
                set_offer_status(offer["id"], "accepted")
                bot.send_message(chat_id, T[lang]["o_accepted_owner"].format(
                    name=esc(offerer["full_name"]), phone=esc(offerer["phone"]), loc=esc(offerer["location"])))
                try:
                    ol = lang_of(offerer["user_id"])
                    bot.send_message(offerer["user_id"], T[ol]["o_accepted_offerer"].format(
                        item=esc(item["title"]), name=esc(owner["full_name"]),
                        phone=esc(owner["phone"]), loc=esc(owner["location"])))
                except Exception:
                    logger.warning("Could not notify offerer %s", offerer["user_id"])
            else:
                set_offer_status(offer["id"], "declined")
                bot.send_message(chat_id, T[lang]["o_declined_owner"])
                try:
                    ol = lang_of(offerer["user_id"])
                    bot.send_message(offerer["user_id"], T[ol]["o_declined"].format(item=esc(item["title"])))
                except Exception:
                    logger.warning("Could not notify offerer %s", offerer["user_id"])
            return

        # ----- my items -----
        if data.startswith("mi:"):
            _, action, item_id = data.split(":")
            item = get_item(int(item_id))
            if not item or item["owner_id"] != uid:
                return
            if action == "sw":
                mark_swapped(item["id"], uid)
                bot.send_message(chat_id, T[lang]["mi_swapped"])
            elif action == "dl":
                kb = types.InlineKeyboardMarkup()
                kb.row(types.InlineKeyboardButton(T[lang]["btn_yes"], callback_data=f"mi:dy:{item['id']}"),
                       types.InlineKeyboardButton(T[lang]["btn_no"], callback_data=f"mi:dn:{item['id']}"))
                bot.send_message(chat_id, T[lang]["mi_confirm"], reply_markup=kb)
            elif action == "dy":
                delete_item(item["id"], uid)
                bot.send_message(chat_id, T[lang]["mi_deleted"])
            elif action == "dn":
                try:
                    bot.delete_message(chat_id, c.message.message_id)
                except Exception:
                    pass
            elif action == "ed":
                kb = types.InlineKeyboardMarkup(row_width=2)
                kb.add(*[types.InlineKeyboardButton(T[lang][f"f_{f}"], callback_data=f"ef:{item['id']}:{f}")
                         for f in EDITABLE])
                bot.send_message(chat_id, T[lang]["ed_choose"], reply_markup=kb)
            return

        if data.startswith("ef:"):
            _, item_id, field = data.split(":", 2)
            item = get_item(int(item_id))
            if not item or item["owner_id"] != uid or field not in EDITABLE:
                return
            states[uid] = {"step": "edit", "data": {"item_id": item["id"], "field": field}}
            bot.send_message(chat_id, T[lang]["ed_ask"], reply_markup=cancel_kb(uid))
            return

        # ================= SOCIAL MEDIA EXCHANGE =================
        if data == "soc:post":
            states[uid] = {"step": "so_platform",
                           "data": {"handle": "-", "followers": "-", "description": "", "price": 0, "wanted": ""}}
            bot.send_message(chat_id, T[lang]["so_platform"], reply_markup=platform_kb(uid, "spl"))
            return

        if data.startswith("spl:"):
            st = states.get(uid)
            plat = data.split(":")[1]
            if not st or st["step"] != "so_platform" or plat not in PLATFORMS:
                return
            st["data"]["platform"] = plat
            st["step"] = "so_type"
            kb = types.InlineKeyboardMarkup()
            kb.row(*[types.InlineKeyboardButton(T[lang]["dt_" + t], callback_data=f"sty:{t}") for t in ("sell", "swap")])
            kb.add(types.InlineKeyboardButton(T[lang]["dt_buy"], callback_data="sty:buy"))
            bot.send_message(chat_id, T[lang]["so_type"], reply_markup=kb)
            return

        if data.startswith("sty:"):
            st = states.get(uid)
            dtype = data.split(":")[1]
            if not st or st["step"] != "so_type" or dtype not in DEAL_TYPES:
                return
            st["data"]["deal_type"] = dtype
            st["step"] = SOCIAL_FLOW[dtype][0]
            social_prompt(uid, chat_id, st["step"], dtype)
            return

        if data == "soc:browse":
            bot.send_message(chat_id, T[lang]["so_choose_platform"], reply_markup=platform_kb(uid, "sbp", with_all=True))
            return

        if data.startswith("sbp:"):
            plat = data.split(":")[1]
            if plat != "all" and plat not in PLATFORMS:
                return
            ids = social_ids(plat)
            if not ids:
                bot.send_message(chat_id, T[lang]["so_none"])
                return
            social_ctx[uid] = {"ids": ids}
            show_social_list(chat_id, uid, 0)
            return

        if data.startswith("sp:"):
            idx = int(data.split(":")[1])
            try:
                bot.delete_message(chat_id, c.message.message_id)
            except Exception:
                pass
            show_social_list(chat_id, uid, idx)
            return

        if data.startswith("sd:"):
            l = get_listing(int(data.split(":")[1]))
            if not l or l["status"] != "active":
                bot.send_message(chat_id, T[lang]["so_unavailable"])
                return
            if l["owner_id"] == uid:
                bot.send_message(chat_id, T[lang]["so_own"])
                return
            if open_deal_exists(l["id"], uid):
                bot.send_message(chat_id, T[lang]["so_deal_exists"])
                return
            states[uid] = {"step": "deal_msg", "data": {"listing_id": l["id"]}}
            bot.send_message(chat_id, T[lang]["so_deal_msg"], reply_markup=cancel_kb(uid))
            return

        if data == "soc:mine":
            rows = user_listings(uid)
            if not rows:
                bot.send_message(chat_id, T[lang]["so_mine_empty"])
                return
            for l in rows:
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton(T[lang]["so_delete"], callback_data=f"smd:{l['id']}"))
                bot.send_message(chat_id, social_caption(l, lang, show_status=True), reply_markup=kb)
            return

        if data.startswith("smd:"):
            lid = int(data.split(":")[1])
            l = get_listing(lid)
            if l and l["owner_id"] == uid:
                delete_listing(lid, uid)
                bot.send_message(chat_id, T[lang]["so_deleted"])
            return

    except Exception:
        logger.exception("on_callback error | data=%s", data)
        try:
            bot.send_message(chat_id, tr(uid, "error"))
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Optional health-check server (Render Web Services need an open port)
# ----------------------------------------------------------------------------
def start_health_server():
    port = os.getenv("PORT")
    if not port:
        return

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"the Lwe bot is running")

        def log_message(self, *args):
            pass

    threading.Thread(
        target=lambda: HTTPServer(("0.0.0.0", int(port)), Handler).serve_forever(),
        daemon=True,
    ).start()
    logger.info("Health server listening on port %s", port)


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    start_health_server()
    try:
        bot.remove_webhook()
    except Exception:
        pass
    logger.info("the Lwe bot started. Polling...")
    bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
