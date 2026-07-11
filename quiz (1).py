import os
import time
import sqlite3
import threading
from datetime import datetime
import pytz  
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

# 📚 दूसरी फाइल से प्रश्न इम्पोर्ट करें
from questions import QUIZ_LIST

# .env से सभी क्रेडेंशियल्स लोड करें
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")
SUPPORT_GROUP_ID = os.getenv("SUPPORT_GROUP_ID")  # 👈 [UPDATED] .env से ग्रुप आईडी लोड करने के लिए

if not API_TOKEN:
    raise ValueError("Error: BOT_TOKEN एनवायरनमेंट वेरिएबल्स में नहीं मिला!")

bot = telebot.TeleBot(API_TOKEN)
DB_FILE = "bot_data.db"

# 🚀 परफ़ॉर्मेंस बूस्ट: ग्लोबल बॉट यूज़रनेम वेरिएबल
BOT_USERNAME = "Bot"
try:
    BOT_USERNAME = bot.get_me().username
except Exception:
    pass

if OWNER_ID:
    try:
        OWNER_ID = int(OWNER_ID)
    except ValueError:
        OWNER_ID = None

# 📌 [UPDATED] ग्रुप आईडी को टेक्स्ट से पूर्णांक (Integer) संख्या में बदलें
if SUPPORT_GROUP_ID:
    try:
        SUPPORT_GROUP_ID = int(SUPPORT_GROUP_ID)
    except ValueError:
        SUPPORT_GROUP_ID = None

# 💾 परमानेंट डेटाबेस आर्किटेक्चर (रीस्टार्ट प्रूफ)
def init_db():
    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS groups (
                chat_id INTEGER PRIMARY KEY,
                current_index INTEGER DEFAULT 0,
                last_poll_id INTEGER DEFAULT NULL,
                last_sent_time REAL DEFAULT 0,
                language TEXT DEFAULT 'hindi',
                interval INTEGER DEFAULT 1800,
                auto_delete INTEGER DEFAULT 1
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT,
                join_time REAL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS poll_mapping (
                poll_id TEXT PRIMARY KEY,
                chat_id INTEGER,
                correct_id INTEGER,
                creation_time REAL DEFAULT 0
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_scores (
                chat_id INTEGER,
                user_id INTEGER,
                user_name TEXT,
                correct_count INTEGER DEFAULT 0,
                wrong_count INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        cursor.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('leaderboard_time', '22:00')")
        
        try:
            cursor.execute("ALTER TABLE poll_mapping ADD COLUMN creation_time REAL DEFAULT 0")
        except sqlite3.OperationalError:
            pass 
            
        conn.commit()

init_db()

def is_user_admin(chat_id, user_id):
    if OWNER_ID and user_id == OWNER_ID:
        return True
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ['creator', 'administrator']
    except Exception:
        return False

# 🚨 [NEW GLOBAL DICTIONARY] हर ग्रुप के लिए वार्निंग टाइमस्टैम्प याद रखने के लिए
NON_ADMIN_WARNING_TRACKER = {}

# 🔄 हर ग्रुप के लिए कस्टमाइज्ड पोल शेड्यूलर लूप
def global_poll_manager():
    while True:
        try:
            with sqlite3.connect(DB_FILE, timeout=20) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT chat_id, current_index, last_poll_id, last_sent_time, language, interval, auto_delete FROM groups")
                all_groups = cursor.fetchall()
                current_now = time.time()

                for chat_id, current_index, last_poll_id, last_sent_time, language, interval, auto_delete in all_groups:
                    if current_now - last_sent_time >= interval:
                        
                        # चेक करें कि क्या बॉट अभी भी ग्रुप में एडメン है?
                        is_bot_admin = False
                        try:
                            bot_member = bot.get_chat_member(chat_id, bot.get_me().id)
                            if bot_member.status in ['administrator', 'creator']:
                                is_bot_admin = True
                        except Exception:
                            is_bot_admin = False

                        # ⚠️ अगर बॉट एडमिन नहीं है
                        if not is_bot_admin:
                            # ⏱️ 12 घंटे = 12 * 60 * 60 = 43200 सेकंड्स
                            # 💡 अगर आपको 6 घंटे करना हो तो 21600 कर देना, 24 घंटे के लिए 86400 कर देना
                            warning_interval = 43200 
                            
                            last_warning_time = NON_ADMIN_WARNING_TRACKER.get(chat_id, 0)
                            
                            # अगर पिछली वार्निंग को भेजे 12 घंटे (43200 सेकंड) हो चुके हैं या यह पहली वार्निंग है
                            if current_now - last_warning_time >= warning_interval:
                                try:
                                    bot.send_message(
                                        chat_id=chat_id, 
                                        text="⚠️ **alert!**\n\nTo send polls in this group, you must re-promote the bot to Admin **(Administrator)** and grant permissions।",
                                        parse_mode="Markdown"
                                    )
                                    # 🎯 इस ग्रुप के लिए करंट वार्निंग टाइम मेमोरी में सेव करें
                                    NON_ADMIN_WARNING_TRACKER[chat_id] = current_now
                                except Exception:
                                    pass
                            
                            # बार-बार डेटाबेस लूप को एक्टिवेट न करने के लिए last_sent_time को नॉर्मल इंटरवल तक बढ़ाएं
                            cursor.execute("UPDATE groups SET last_sent_time = ? WHERE chat_id = ?", (current_now, chat_id))
                            conn.commit()
                            continue  # इस ग्रुप को स्किप करें

                        # --- पुराना पोल डिलीट करने का लॉजिक (एडमिन होने पर ही चलेगा) ---
                        if last_poll_id is not None and auto_delete == 1:
                            try:
                                bot.delete_message(chat_id=chat_id, message_id=last_poll_id)
                            except Exception:
                                pass

                        filtered_quiz = [q for q in QUIZ_LIST if q.get("lang", "hindi") == language]
                        if not filtered_quiz:
                            filtered_quiz = QUIZ_LIST

                        if current_index >= len(filtered_quiz):
                            current_index = 0

                        quiz = filtered_quiz[current_index]
                        explanation_text = quiz.get("explanation", None)
                        
                        try:
                            sent_message = bot.send_poll(
                                chat_id=chat_id,
                                question=quiz["question"],
                                options=quiz["options"],
                                type="quiz",
                                correct_option_id=quiz["correct_id"],
                                is_anonymous=False,  
                                explanation=explanation_text
                            )
                            new_poll_id = sent_message.message_id
                            poll_api_id = sent_message.poll.id
                            
                            cursor.execute("INSERT INTO poll_mapping (poll_id, chat_id, correct_id, creation_time) VALUES (?, ?, ?, ?)", 
                                           (poll_api_id, chat_id, quiz["correct_id"], time.time()))

                            new_index = (current_index + 1) % len(filtered_quiz)
                            cursor.execute('''
                                UPDATE groups 
                                SET current_index = ?, last_poll_id = ?, last_sent_time = ? 
                                WHERE chat_id = ?
                            ''', (new_index, new_poll_id, current_now, chat_id))
                            conn.commit()

                        except Exception as e:
                            if "bot was kicked" in str(e).lower() or "chat not found" in str(e).lower():
                                cursor.execute("DELETE FROM groups WHERE chat_id = ?", (chat_id,))
                                conn.commit()
        except Exception as db_err:
            print(f"डेटाबेस लूप एरर: {db_err}")
        time.sleep(5)
        

# ⚙️ मुख्य सेटिंग्स मेनू यूआई जेनरेटर
def get_settings_markup(chat_id):
    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT language, interval, auto_delete FROM groups WHERE chat_id = ?", (chat_id,))
        res = cursor.fetchone()
    if not res: return None, None
    lang, interval, auto_delete = res[0], res[1], res[2]
    interval_mins = interval // 60
    del_status = "ON ✅" if auto_delete == 1 else "OFF 📴"
    
    text = (
        "⚙️ **Settings Panel (Quiz Settings)**\n\n"
        f"🌐 **Current Language:** {lang.upper()}\n"
        f"⏱️ **Quiz Interval:** {interval_mins} min\n"
        f"🗑️ **Auto Delete Poll:** {del_status}\n\n"
        "Click on the buttons below to change configurations:"
    )
    markup = InlineKeyboardMarkup()
    lang_text = "🌐 भाषा: HINDI 🇮🇳" if lang == 'hindi' else "🌐 Lang: ENGLISH 🇬🇧"
    btn_lang = InlineKeyboardButton(text=lang_text, callback_data=f"set_lang_{chat_id}")
    btn_autodel = InlineKeyboardButton(text="🗑️ Auto-Delete Settings", callback_data=f"menu_autodel_{chat_id}")
    btn_15m = InlineKeyboardButton(text="⏱️ 15 Min", callback_data=f"set_time_900_{chat_id}")
    btn_30m = InlineKeyboardButton(text="⏱️ 30 Min", callback_data=f"set_time_1800_{chat_id}")
    btn_45m = InlineKeyboardButton(text="⏱️ 45 Min", callback_data=f"set_time_2700_{chat_id}")
    btn_60m = InlineKeyboardButton(text="⏱️ 60 Min", callback_data=f"set_time_3600_{chat_id}")
    btn_close = InlineKeyboardButton(text="Close ❌", callback_data=f"panel_close_{chat_id}")
    markup.row(btn_lang)
    markup.row(btn_autodel)
    markup.row(btn_15m, btn_30m)
    markup.row(btn_45m, btn_60m)
    markup.row(btn_close)
    return text, markup

# 🗑️ ऑटो-डिलीट सेटिंग्स का सब-मेनू जेनरेटर
def get_autodelete_markup(chat_id):
    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT auto_delete FROM groups WHERE chat_id = ?", (chat_id,))
        res = cursor.fetchone()
    auto_delete = res[0] if res else 1
    status_text = "ON" if auto_delete == 1 else "OFF"
    text = (
        "🗑️ **Auto-Delete Settings**\n\n"
        "⚠️ **Click on the control buttons**\n\n"
        f"📊 **Status:** \" {status_text} \"\n\n"
        "ℹ️ **What does this do?**\n"
        "• When ON: Previous quiz poll will be deleted automatically.\n"
        "• When OFF: Old quizzes will stay in chat history.\n\n"
        "👇 Toggle auto-delete setting:"
    )
    markup = InlineKeyboardMarkup()
    btn_on = InlineKeyboardButton(text="Turn On ✅", callback_data=f"autodel_on_{chat_id}")
    btn_off = InlineKeyboardButton(text="Turn Off 📴", callback_data=f"autodel_off_{chat_id}")
    btn_back = InlineKeyboardButton(text="Back 🔙", callback_data=f"autodel_back_{chat_id}")
    markup.row(btn_on, btn_off)
    markup.row(btn_back)
    return text, markup

@bot.message_handler(commands=['settings'])
def group_settings(message):
    chat_type = message.chat.type

    # 🚨 [NEW UPDATE] अगर कोई यूजर बॉट की पर्सनल चैट (DM) में /settings डालता है
    if chat_type == 'private':
        try:
            bot.reply_to(message, "❌ This command can only be used in groups.")
        except Exception:
            pass
        return  # फंक्शन यहीं रुक जाएगा, सेटिंग्स पैनल ओपन नहीं होगा

    # ग्रुप के अंदर एडमिन चेक करने का लॉजिक (यह पहले से है)
    if not is_user_admin(message.chat.id, message.from_user.id):
        try: bot.reply_to(message, "❌ Only group admin's can change the settings.")
        except Exception: pass
        return
        
    text, markup = get_settings_markup(message.chat.id)
    if text: 
        try: bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")
        except Exception: pass

# 🔄 सेटिंग्स बटन प्रोसेसर (मल्टी-इंडेक्स आर्किटेक्चर फिक्स्ड)
@bot.callback_query_handler(func=lambda call: call.data.startswith(('set_lang_', 'set_time_', 'menu_autodel_', 'autodel_', 'panel_close_')))
def handle_settings_callbacks(call):
    user_id = call.from_user.id
    data_parts = call.data.split('_')
    
    action = data_parts[0]       
    sub_action = data_parts[1]   
    chat_id = int(data_parts[-1]) 
    
    if not is_user_admin(chat_id, user_id):
        bot.answer_callback_query(call.id, "❌ You do not have admin permissions!", show_alert=True)
        return

    if action == "panel" and sub_action == "close":
        try: bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)
        except Exception: pass
        return

    show_main_menu = True
    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        cursor = conn.cursor()
        
        if action == "set" and sub_action == "lang":
            cursor.execute("SELECT language FROM groups WHERE chat_id = ?", (chat_id,))
            res = cursor.fetchone()
            current_lang = res[0] if res else 'hindi'
            new_lang = 'english' if current_lang == 'hindi' else 'hindi'
            cursor.execute("UPDATE groups SET language = ? WHERE chat_id = ?", (new_lang, chat_id))
            bot.answer_callback_query(call.id, f"भाषा बदलकर {new_lang.upper()} कर दी गई है।")
            
        elif action == "set" and sub_action == "time":
            new_interval = int(data_parts[2]) 
            cursor.execute("UPDATE groups SET interval = ? WHERE chat_id = ?", (new_interval, chat_id))
            bot.answer_callback_query(call.id, f"समय अंतराल बदलकर {new_interval // 60} मिनट कर दिया गया है।")
            
        elif action == "menu" and sub_action == "autodel":
            show_main_menu = False
            bot.answer_callback_query(call.id) 
            
        elif action == "autodel":
            if sub_action == "on":
                cursor.execute("UPDATE groups SET auto_delete = 1 WHERE chat_id = ?", (chat_id,))
                bot.answer_callback_query(call.id, "Auto-Delete चालू (ON) कर दिया गया है।")
                show_main_menu = False
            elif sub_action == "off":
                cursor.execute("UPDATE groups SET auto_delete = 0 WHERE chat_id = ?", (chat_id,))
                bot.answer_callback_query(call.id, "Auto-Delete बंद (OFF) कर दिया गया है।")
                show_main_menu = False
            elif sub_action == "back":
                bot.answer_callback_query(call.id, "मुख्य मेनू पर वापस जा रहे हैं...")
                show_main_menu = True
                
        conn.commit()
        
    if show_main_menu: text, markup = get_settings_markup(chat_id)
    else: text, markup = get_autodelete_markup(chat_id)
        
    try: bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=text, reply_markup=markup, parse_mode="Markdown")
    except Exception: pass

# 👑 ओनर कमांड - टाइम सेट करना (Strict Group & Owner Security Added)
@bot.message_handler(commands=['settime'])
def set_global_leaderboard_time(message):
    is_owner = (OWNER_ID and message.from_user.id == OWNER_ID)
    is_valid_chat = (message.chat.type == 'private' or (SUPPORT_GROUP_ID and message.chat.id == SUPPORT_GROUP_ID))

    if not (is_owner and is_valid_chat):
        try: bot.send_message(message.chat.id, "❌ This command is only valid for the bot owner and in authorized chats.")
        except Exception: pass
        return
    
    args = message.text.split()
    if len(args) < 2:
        bot.send_message(message.chat.id, "⚠️ **गलत फॉर्मेट!**\nकृपया इस तरह लिखें: `/settime HH:MM` \nउदाहरण: `/settime 22:00`", parse_mode="Markdown")
        return
        
    time_str = args[1].strip()
    try:
        datetime.strptime(time_str, "%H:%M")
        with sqlite3.connect(DB_FILE, timeout=20) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE bot_settings SET value = ? WHERE key = 'leaderboard_time'", (time_str,))
            conn.commit()
        bot.send_message(message.chat.id, f"✅ **Chief, the time has been updated!**\nFrom now on, daily results will be auto-sent at exactly **{time_str}**", parse_mode="Markdown")
    except ValueError:
        bot.send_message(message.chat.id, "❌ **Invalid time format!**\nPlease use the 24-hour format.(ex: 13:00, 22:30)।")

# 👑 📢 ओनर कमांड - अपडेटेड ब्रॉडकास्ट फ़ीचर (Strict Group & Owner Security Added)
@bot.message_handler(commands=['broadcast'])
def handle_owner_broadcast(message):
    is_owner = (OWNER_ID and message.from_user.id == OWNER_ID)
    is_valid_chat = (message.chat.type == 'private' or (SUPPORT_GROUP_ID and message.chat.id == SUPPORT_GROUP_ID))

    if not (is_owner and is_valid_chat):
        try: bot.send_message(message.chat.id, "❌ This command is only valid for the bot owner and in authorized chats.")
        except Exception: pass
        return

    if not message.reply_to_message:
        bot.send_message(
            message.chat.id, 
            "⚠️ **उपयोग कैसे करें?**\n"
            "1. वह टेक्स्ट, फोटो, वीडियो या स्टिकर भेजें जिसे ब्रॉडकास्ट करना है।\n"
            "2. उस मैसेज पर **Reply** करके लिखें: `/broadcast`", 
            parse_mode="Markdown"
        )
        return

    target_msg = message.reply_to_message
    status_msg = bot.send_message(message.chat.id, "📢 **Initializing broadcast process, please wait....**", parse_mode="Markdown")

    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id FROM groups")
        all_chats = cursor.fetchall()
        cursor.execute("SELECT user_id FROM users")
        all_users = cursor.fetchall()

    g_success, g_fail = 0, 0
    u_success, u_fail = 0, 0

    for (chat_id,) in all_chats:
        try:
            bot.copy_message(chat_id=chat_id, from_chat_id=message.chat.id, message_id=target_msg.message_id)
            g_success += 1
            time.sleep(0.15)  
        except Exception: g_fail += 1

    for (user_id,) in all_users:
        try:
            bot.copy_message(chat_id=user_id, from_chat_id=message.chat.id, message_id=target_msg.message_id)
            u_success += 1
            time.sleep(0.15)  
        except Exception: u_fail += 1

    bot.edit_message_text(
        chat_id=message.chat.id, 
        message_id=status_msg.message_id, 
        text=f"📊 **Global Broadcast Report:**\n\n"
             f"👥 **group's:**\n"
             f"✅ **done: {g_success}** | ❌ **Undone: {g_fail}**\n\n"
             f"👤 **Private User's:**\n"
             f"✅ **done: {u_success}** | ❌ **Undone: {u_fail}**\n\n"
             f"🎯 **Broadcast completed successfully!**", 
        parse_mode="Markdown"
    )
    

@bot.message_handler(commands=['sendresult'])
def manual_leaderboard_sender(message):
    is_owner = (OWNER_ID and message.from_user.id == OWNER_ID)
    is_valid_chat = (message.chat.type == 'private' or (SUPPORT_GROUP_ID and message.chat.id == SUPPORT_GROUP_ID))

    if not (is_owner and is_valid_chat):
        try: bot.send_message(message.chat.id, "❌ This command is only valid for the bot owner and in authorized chats.")
        except Exception: pass
        return
        
    status_msg = bot.send_message(message.chat.id, "⏳ **Sending new result to all groups immediately...**")
    IST = pytz.timezone('Asia/Kolkata')
    now = datetime.now(IST)
    
    markup = InlineKeyboardMarkup()

# [CORRECTED] 't.me' के बाद '/' लगाया ताकि सही URL बने
add_to_group_url = f"https://t.me/{BOT_USERNAME}?startgroup=true"

# [UPDATED] style="success" जोड़कर बटन का बैकग्राउंड हरा (Green) किया गया है
markup.add(InlineKeyboardButton(
    text="➕ Add Me To Your Group ➕", 
    url=add_to_group_url,
    style="success"
))

    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id FROM groups")
        all_chats = cursor.fetchall()
        success_count = 0
        
        for (chat_id,) in all_chats:
            cursor.execute("SELECT user_name, correct_count, wrong_count FROM daily_scores WHERE chat_id = ?", (chat_id,))
            all_users = cursor.fetchall()
            
            calculated_leaderboard = []
            for name, correct, wrong in all_users:
                final_score = (correct * 2) - (wrong * 0.5)
                if (correct + wrong) > 0:
                    # [CORRECTED] सॉर्टिंग सही करने के लिए final_score को टुपल में सबसे आगे रखा
                    calculated_leaderboard.append((final_score, name, correct, wrong))
            
            # [CORRECTED] अब सबसे ज़्यादा स्कोर वाले यूज़र्स बिल्कुल टॉप पर दिखेंगे
            calculated_leaderboard.sort(key=lambda x: x[0], reverse=True)
            top_20 = calculated_leaderboard[:20]
            
            lb_text = "🏆 **Result [Top 20 user's Leaderboard]**\n"
            lb_text += f"---------------------------------------\n" 
            lb_text += f"📅 Date: {now.strftime('%d-%m-%Y')} | ⏰ Time: {now.strftime('%H:%M')} (Manual)\n"
            lb_text += "📊 Marking: Right (+2) | Wrong (-0.5)\n"
            lb_text += f"---------------------------------------\n\n" 
            
            if top_20:
                medals = {1: "🥇", 2: "🥈", 3: "🥉"}
                for idx, (final_score, name, correct, wrong) in enumerate(top_20, 1):
                    medal = medals.get(idx, f"{idx}.")
                    # स्कोर फ़ॉर्मेट (.0 हटाने के लिए)
                    display_score = f"{final_score:.1f}" if final_score % 0.5 != 0 else f"{int(final_score)}"
                    
                    lb_text += f"{medal} **{name}**\n"
                    lb_text += f"🔥 Score: **{display_score}** pts | ✅ {correct} | ❌ {wrong}\n"
                    lb_text += f"---------------------------------------\n" 
            else:
                lb_text += "⚠️ No users participated in the quiz today.\n"
                lb_text += f"---------------------------------------\n"
                
            lb_text += "\n🎯 Amazing effort! Get ready for a new quiz tomorrow! 🚀"
            try: 
                bot.send_message(chat_id=chat_id, text=lb_text, reply_markup=markup, parse_mode="Markdown")
                success_count += 1
                time.sleep(0.15)
            except Exception: pass
            
        cursor.execute("DELETE FROM daily_scores")
        cursor.execute("DELETE FROM poll_mapping")
        conn.commit()
    bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg.message_id, text=f"✅ **Chief, the manual result has been successfully sent.!**\n📊 total **{success_count}** Leaderboard sent to active groups and scores have been reset!", parse_mode="Markdown")

def daily_leaderboard_scheduler():
    has_sent_today = False
    last_checked_date = ""
    
    markup = InlineKeyboardMarkup()
    
    # [FIXED] BOT_USERNAME के पहले स्लैश (/) मिसिंग था जिससे URL गलत बन रहा था, उसे ठीक किया
    add_to_group_url = f"https://t.me/{BOT_USERNAME}?startgroup=true"
    
    # [UPDATED] style="success" जोड़कर बटन का बैकग्राउंड हरा (Green) किया गया है
    markup.add(InlineKeyboardButton(
        text="Add Me To Your Group", 
        url=add_to_group_url,
        style="success"
    ))
    
    while True:
        try:
            IST = pytz.timezone('Asia/Kolkata')
            now = datetime.now(IST)
            current_date_str = now.strftime("%Y-%m-%d")
            
            if current_date_str != last_checked_date:
                has_sent_today = False
                last_checked_date = current_date_str

            with sqlite3.connect(DB_FILE, timeout=20) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM bot_settings WHERE key = 'leaderboard_time'")
                res = cursor.fetchone()
                # [CORRECTED] डेटाबेस से टुपल की जगह साफ़ स्ट्रिंग निकालने के लिए res[0] किया
                db_time = res[0] if res else "22:00"
            
            try: target_hour, target_minute = map(int, db_time.split(':'))
            except Exception: target_hour, target_minute = 22, 0
            
            if now.hour == target_hour and now.minute == target_minute and not has_sent_today:
                with sqlite3.connect(DB_FILE, timeout=20) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT chat_id FROM groups")
                    all_chats = cursor.fetchall()
                    
                    for (chat_id,) in all_chats:
                        cursor.execute("SELECT user_name, correct_count, wrong_count FROM daily_scores WHERE chat_id = ?", (chat_id,))
                        all_users = cursor.fetchall()
                        
                        calculated_leaderboard = []
                        for name, correct, wrong in all_users:
                            final_score = (correct * 2) - (wrong * 0.5)
                            if (correct + wrong) > 0:
                                # [CORRECTED] सॉर्टिंग सही करने के लिए final_score को टुपल में सबसे आगे रखा
                                calculated_leaderboard.append((final_score, name, correct, wrong))
                                
                        # [CORRECTED] अब सबसे ज़्यादा स्कोर वाले यूज़र्स बिल्कुल टॉप पर दिखेंगे
                        calculated_leaderboard.sort(key=lambda x: x[0], reverse=True)
                        top_20 = calculated_leaderboard[:20]
                        
                        lb_text = "🏆 **Result [Top 20 user's Leaderboard]**\n"
                        lb_text += f"---------------------------------------\n" 
                        lb_text += f"📅 Date: {now.strftime('%d-%m-%Y')} | ⏰ Time: {db_time}\n"
                        lb_text += "🎓 Performance of the Last 24 Hours:\n"
                        lb_text += "📊 Marking: Right (+2) | Wrong (-0.5)\n"
                        lb_text += f"---------------------------------------\n\n" 
                        
                        if top_20:
                            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
                            for idx, (final_score, name, correct, wrong) in enumerate(top_20, 1):
                                medal = medals.get(idx, f"{idx}.")
                                # स्कोर फ़ॉर्मेट (.0 हटाने के लिए)
                                display_score = f"{final_score:.1f}" if final_score % 0.5 != 0 else f"{int(final_score)}"
                                
                                lb_text += f"{medal} **{name}**\n"
                                lb_text += f"🔥 Score: **{display_score}** point | ✅ {correct} | ❌ {wrong}\n"
                                lb_text += f"---------------------------------------\n" 
                        else:
                            lb_text += "⚠️ No users participated in the quiz today.\n"
                            lb_text += f"---------------------------------------\n"
                            
                        lb_text += "\n🎯 Amazing effort! Get ready for a new quiz tomorrow! 🚀"
                        try: 
                            bot.send_message(chat_id=chat_id, text=lb_text, reply_markup=markup, parse_mode="Markdown")
                            time.sleep(0.15)
                        except Exception: pass
                            
                    cursor.execute("DELETE FROM daily_scores")
                    cursor.execute("DELETE FROM poll_mapping")
                    conn.commit()
                    
                has_sent_today = True
                time.sleep(60) 
                
        except Exception as sched_err:
            print(f"शेड्यूलर एरर: {sched_err}")
        time.sleep(20)
        
# 🎯 LIVE पोल उत्तर ट्रैकर (OLD POLL STOPPER FEATURE LOADED ✅)
@bot.poll_answer_handler()
def handle_poll_answer(poll_answer):
    poll_id = poll_answer.poll_id
    user_id = poll_answer.user.id
    
    first_name = poll_answer.user.first_name if poll_answer.user.first_name else ""
    last_name = poll_answer.user.last_name if poll_answer.user.last_name else ""
    user_name = f"{first_name} {last_name}".strip()
    if not user_name: user_name = f"User_{user_id}"

    if not poll_answer.option_ids:
        return

    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id, correct_id, creation_time FROM poll_mapping WHERE poll_id = ?", (poll_id,))
        mapping = cursor.fetchone()
        
        if not mapping:
            return  

        if mapping and poll_answer.option_ids:
            chat_id = mapping[0]
            correct_id = mapping[1]
            creation_time = mapping[2]
            chosen_option = poll_answer.option_ids[0]
            
            if time.time() - creation_time > 86400:
                return  

            if chosen_option == correct_id:
                cursor.execute('''
                    INSERT INTO daily_scores (chat_id, user_id, user_name, correct_count, wrong_count)
                    VALUES (?, ?, ?, 1, 0)
                    ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    user_name = excluded.user_name,
                    correct_count = correct_count + 1
                ''', (chat_id, user_id, user_name))
            else:
                cursor.execute('''
                    INSERT INTO daily_scores (chat_id, user_id, user_name, correct_count, wrong_count)
                    VALUES (?, ?, ?, 0, 1)
                    ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    user_name = excluded.user_name,
                    wrong_count = wrong_count + 1
                ''', (chat_id, user_id, user_name))
            conn.commit()

# 📊 यूजर लाइव स्कोर ट्रैकर कस्टमाइज्ड कमांड (प्राइवेट चैट ब्लॉक के साथ)
@bot.message_handler(commands=['myscore'])
def check_user_score(message):
    chat_type = message.chat.type

    # 🚨 [UPDATED] अगर यूजर प्राइवेट चैट (DM) में कमांड डालता है
    if chat_type == 'private':
        try:
            bot.reply_to(message, "❌ This command can only be used in groups.")
        except Exception:
            pass
        return  # फंक्शन यहीं रुक जाएगा, स्कोर नहीं दिखेगा

    user_id = message.from_user.id
    chat_id = message.chat.id

    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT correct_count, wrong_count FROM daily_scores WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        res = cursor.fetchone()
    
    if res:
        correct = res[0]
        wrong = res[1]
        final_score = (correct * 2) - (wrong * 0.5)
    else:
        correct, wrong, final_score = 0, 0, 0.0

    try: 
        score_text = (
            f"🇮🇳 **hello {message.from_user.first_name}**, your today's quiz score!\n\n"
            f"✅ Correct Ans: **{correct}** (+{correct * 2} point)\n"
            f"❌ Wrong Ans: **{wrong}** (-{wrong * 0.5} point)\n"
            f"🔥 **Final Score: {final_score} point**\n\n"
            f"ℹ️ Note: This score will be reset after the leaderboard is published.\n"
            f"If you don't want to wait for the results, you can use the `/myscore` command at any time."
        )
        bot.reply_to(message, score_text, parse_mode="Markdown")
    except Exception: 
        pass

# 💬 /start कमांड (Strict Group Validation के साथ 100% FIXED)
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    chat_type = message.chat.type
    message_text = message.text.strip() if message.text else ""
    
    # 🚨 [CRITICAL FIX] चेक करें कि क्या कमांड सिर्फ इसी बॉट के लिए है?
    # अगर ग्रुप में कोई दूसरा बॉट ट्रिगर हुआ है (जैसे /start@OtherBot), तो यह फ़ंक्शन यहीं रुक जाएगा!
    if chat_type in ['group', 'supergroup']:
        expected_full_command = f"/start@{BOT_USERNAME}"
        # अगर सिर्फ '/start' है तो ठीक, लेकिन अगर '@' लगा है और वो इस बॉट का नाम नहीं है तो रिजेक्ट करें
        if "@" in message_text and not message_text.startswith(expected_full_command):
            return  # ❌ दूसरे बॉट की कमांड है, मेरा बॉट शांत रहेगा

    first_name = message.from_user.first_name if message.from_user.first_name else ""
    last_name = message.from_user.last_name if message.from_user.last_name else ""
    full_name = f"{first_name} {last_name}".strip()
    if not full_name: full_name = f"User_{user_id}"

    # 📌 अगर बॉट को इसी ग्रुप में सही तरीके से /start किया जाए
    if chat_type in ['group', 'supergroup']:
        group_text = (
            f"🎉 **Bot activated successfully!**\n"
            f"📢 Automated quizzes have been activated for this group.\n\n"
            f"🇮🇳 **Group Name:** [{message.chat.title}]\n"
            f"This bot is the easiest way to keep your groups active and engaged.\n\n"
            f"📌 **My Features:**\n"
            f"📊 **Daily Auto Poll:** Automatically sends a new poll every day at your set time interval.\n"
            f"🏆 **Auto Result:** Generates results daily at 10 PM showing the Top 20 users' scores with negative marking.\n\n"
            f"🚀 **How to Get Started:**\n"
            f"1. Make me a **Group Admin** (so I have permission to send polls).\n"
            f"2. Use the `/settings` command inside your group to configure everything.\n\n"
            f"For any help, simply type `/help`."
        )
        group_markup = InlineKeyboardMarkup()
        add_to_group_url = f"https://t.me/{BOT_USERNAME}?startgroup=true"
        group_markup.add(InlineKeyboardButton(text="➕ Add Me To Your Group ➕", url=add_to_group_url))
        try: bot.send_message(chat_id=message.chat.id, text=group_text, reply_markup=group_markup, parse_mode="Markdown")
        except Exception: pass
        return  

    # प्राइवेट चैट का बाकी लॉजिक (यह वैसे ही रहेगा)
    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id, user_name, join_time) VALUES (?, ?, ?)", (user_id, full_name, time.time()))
        conn.commit()

    if OWNER_ID and user_id == OWNER_ID:
        with sqlite3.connect(DB_FILE, timeout=20) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM bot_settings WHERE key = 'leaderboard_time'")
            res = cursor.fetchone()
            db_time = res if res else "22:00"
            
        welcome_text = (
            f"👑 **प्रणाम मालिक ({message.from_user.first_name})!**\n\n"
            f"📊 वर्तमान लीडरबोर्ड टाइम: **{db_time}**\n"
            "⚙️ आप सीधे यहीं पर `/settime HH:MM` लिखकर टाइम बदल सकते हैं।\n"
            "🏆 तुरंत रिज़ल्ट भेजने और स्कोर रीसेट करने के लिए `/sendresult` लिखें।\n"
            "📢 किसी भी मैसेज पर रिप्लाई करके `/broadcast` लिखने से वह सभी ग्रुप्स और यूज़र्स के पर्सनल इनबॉक्स में चला जाएगा।\n"
            "📊 बॉट का लाइव स्टैट्स देखने के लिए `/status` का उपयोग करें।\n\n"
            "बॉट को ग्रुप में जोड़ने के लिए नीचे दिए बटन का उपयोग करें।"
        )
    else:
        welcome_text = (
            f"👋 **Hello** {message.from_user.first_name}!\n"
            f"**Welcome!** This bot is the easiest way to keep your groups active and engaged.\n\n"
            f"**📌 My Features:**\n\n"
            f"📊 **Daily Auto Poll:**\n"
            "Automatically sends a new poll every day at your set time interval.\n\n"
            "🏆 **Auto Result:**\n"
            "Generates results daily at 10 PM showing the Top 20 users' scores with negative marking.\n\n"
            "🚀 **How to Get Started:**\n\n"
            "**1. Add me** to your Telegram group.\n"
            "**2. Make me a **Group Admin** (so I have permission to send polls).\n"
            "**3. Use the `/settings` command inside your group to configure everything.**\n\n"
            "For any help, simply type `/help` ."
        )
    markup = InlineKeyboardMarkup()
    add_to_group_url = f"https://t.me/{BOT_USERNAME}?startgroup=true"
    markup.add(InlineKeyboardButton(text="➕ Add Me To Your Group ➕", url=add_to_group_url))
    try: bot.send_message(chat_id=message.chat.id, text=welcome_text, reply_markup=markup, parse_mode="Markdown")
    except Exception: pass
                

# ℹ️ हेल्प कमांड (Strict Username Validation के साथ FIXED)
@bot.message_handler(commands=['help'])
def send_help(message):
    chat_type = message.chat.type
    message_text = message.text.strip() if message.text else ""
    
    # 🚨 [CRITICAL FIX] चेक करें कि क्या कमांड सिर्फ इसी बॉट के लिए है?
    # अगर ग्रुप में कोई दूसरा बॉट ट्रिगर हुआ है (जैसे /help@OtherBot), तो यह फ़ंक्शन यहीं रुक जाएगा!
    if chat_type in ['group', 'supergroup']:
        expected_full_command = f"/help@{BOT_USERNAME}"
        if "@" in message_text and not message_text.startswith(expected_full_command):
            return  # ❌ दूसरे बॉट की कमांड है, मेरा बॉट शांत रहेगा

    help_text = (
        "⚡ **Help & Guide - Daily Poll Bot:**\n\n"
        "Here is a quick guide on how to configure and use the bot in your group:\n\n"
        "🛠 **Setup Instructions:**\n\n"
        "**Step 1:** Add this bot to your group.\n"
        "**Step 2:** Grant the bot Admin Permissions.\n"
        "**Step 3:** Type `/settings` inside the group to set up your poll timing and quiz language.\n\n"
        "🕒 **How the System Works:**\n\n"
        "**Polls:** Sent automatically during your configured daytime intervals.\n"
        "**Leaderboard:** Published automatically every single night at **10:00 PM.**\n"
        "Scoring: Accuracy matters! The leaderboard calculates the Top 20 users with a **negative marking system** applied for wrong answers.\n\n"
        "🔐 `/settings` - Open the configuration panel (Group Admins only)."
    )
    markup = InlineKeyboardMarkup()
    owner_url = "https://t.me/comeback_009"
    markup.add(InlineKeyboardButton(text="Contact Support", url=owner_url))
    try: bot.send_message(chat_id=message.chat.id, text=help_text, reply_markup=markup, parse_mode="Markdown")
    except Exception: pass
        

# 📊 लाइव स्टेटस कमांड (Strict Group & Owner Security Added)
@bot.message_handler(commands=['status'])
def send_stats(message):
    is_owner = (OWNER_ID and message.from_user.id == OWNER_ID)
    is_valid_chat = (message.chat.type == 'private' or (SUPPORT_GROUP_ID and message.chat.id == SUPPORT_GROUP_ID))

    if is_owner and is_valid_chat:
        with sqlite3.connect(DB_FILE, timeout=20) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM groups")
            res_g = cursor.fetchone()
            g_count = res_g[0] if res_g else 0
            
            cursor.execute("SELECT COUNT(*) FROM users")
            res_u = cursor.fetchone()
            u_count = res_u[0] if res_u else 0
            
        bot.send_message(
            message.chat.id, 
            f"📊 **Bot live status:**\n\n"
            f"🎯 Total Active Groups: **{g_count}**\n"
            f"👤 Total Active Users: **{u_count}**"
        )
    else:
        try: bot.send_message(message.chat.id, "❌ This command is only valid for the bot owner and in authorized chats.")
        except Exception: pass
            

# 🤖 ग्रुप जॉइन/लीव ट्रैकर (सेम वेलकम मैसेज आर्किटेक्चर)
@bot.my_chat_member_handler()
def handle_left_or_joined(message):
    new_status = message.new_chat_member.status
    with sqlite3.connect(DB_FILE, timeout=20) as conn:
        cursor = conn.cursor()
        if new_status in ["administrator", "member"]:
            cursor.execute("INSERT OR IGNORE INTO groups (chat_id, interval) VALUES (?, 1800)", (message.chat.id,))
            cursor.execute("UPDATE groups SET last_sent_time = 0 WHERE chat_id = ?", (message.chat.id,))
            conn.commit()
            
            # 📌 [UPDATED] यहाँ भी बिल्कुल वही मैसेज सेट कर दिया गया है जो /start में आता है
            group_text = (
                f"🎉 **Join Group Successfully!**\n"
                f"📢 Automated quizzes have been activated for this group.\n\n"
                f"🇮🇳 **Group Name:** [{message.chat.title}]\n"
                f"This bot is the easiest way to keep your groups active and engaged.\n\n"
                f"📌 **My Features:**\n"
                f"📊 **Daily Auto Poll:** Automatically sends a new poll every day at your set time interval.\n"
                f"🏆 **Auto Result:** Generates results daily at 10 PM showing the Top 20 users' scores with negative marking.\n"
                f"💡 **Results** ka wait nahi karna chahte to `/myscore` command send kare!\n\n"
                f"🚀 **How to Get Started:**\n"
                f"1. Make me a **Group Admin** (so I have permission to send polls).\n"
                f"2. Use the `/settings` command inside your group to configure everything.\n\n"
                f"For any help, simply type `/help`."
            )
            group_markup = InlineKeyboardMarkup()
            add_to_group_url = f"https://t.me/{BOT_USERNAME}?startgroup=true"
            group_markup.add(InlineKeyboardButton(text="➕ Add Me To Your Group ➕", url=add_to_group_url))
            
            try:
                bot.send_message(chat_id=message.chat.id, text=group_text, reply_markup=group_markup, parse_mode="Markdown")
            except Exception: pass
        elif new_status in ["left", "kicked"]:
            cursor.execute("DELETE FROM groups WHERE chat_id = ?", (message.chat.id,))
            conn.commit()

# ❤️‍🩹 थ्रेड्स स्टार्ट करें
threading.Thread(target=global_poll_manager, daemon=True).start()
threading.Thread(target=daily_leaderboard_scheduler, daemon=True).start()

print("Successfully 🇮🇳 deployed...🚀 check kare ✅")

bot.infinity_polling(timeout=60, long_polling_timeout=60)
