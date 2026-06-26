"""
ClipperPay Bot — полная версия с категориями
pip install python-telegram-bot==20.7 psycopg2-binary
"""

import logging
import os
import re
import psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

BOT_TOKEN = "8977496477:AAGLEt-bCmoODGZ3jLDcgRkzCnL6gvTfJXQ"
ADMIN_ID = 5479179057
DATABASE_URL = os.environ.get("DATABASE_URL")

# Состояния
(
    ST_WAIT_NICKNAME,       # нарезчик вводит никнейм
    ST_CUTTER_VIDEO,        # нарезчик отправляет видео
    ST_REJECT_REASON,       # админ пишет причину отказа
    ST_WALLET,              # нарезчик вводит кошелёк
    ST_ADMIN_SET_CAT,       # админ назначает категорию
    ST_ADMIN_USER_ID,       # админ вводит ID участника
    ST_ADMIN_SET_BAL,       # админ устанавливает баланс
    ST_ADMIN_ADD_BAL,       # админ добавляет баланс
    ST_ADMIN_NICKNAME,      # админ вводит кличку стримера
    ST_ADMIN_ASSIGN,        # админ назначает стримера нарезчику
    ST_ADMIN_BROADCAST,     # админ пишет рассылку
    ST_STREAMER_LINK,       # стример вводит twitch ссылку
    ST_STREAMER_RATE,       # стример ставит оценку видео
    ST_STREAMER_BAD_REASON, # стример пишет причину плохой оценки
) = range(14)

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Валюты нарезчика
CUTTER_CUR = {
    "rub": {"name": "Рубль 🇷🇺", "symbol": "₽", "reward": 80},
    "uah": {"name": "Гривна 🇺🇦", "symbol": "₴", "reward": 50},
    "usd": {"name": "Доллар 🇺🇸", "symbol": "$", "reward": 1.0},
    "eur": {"name": "Евро 🇪🇺",   "symbol": "€", "reward": 0.90},
}
# Валюты стримера
STREAMER_CUR = {
    "rub": {"name": "Рубль 🇷🇺", "symbol": "₽", "price": 350},
    "uah": {"name": "Гривна 🇺🇦", "symbol": "₴", "price": 210},
    "usd": {"name": "Доллар 🇺🇸", "symbol": "$", "price": 4.60},
    "eur": {"name": "Евро 🇪🇺",   "symbol": "€", "price": 4.05},
}
# Конвертация нарезчика в базовую единицу (рубли)
CUTTER_TO_RUB = {"rub": 1, "uah": 80/50, "usd": 80, "eur": 80/0.90}
# Конвертация стримера в базовую единицу (рубли)
STREAMER_TO_RUB = {"rub": 1, "uah": 350/210, "usd": 350/4.60, "eur": 350/4.05}

RANKS = {
    "newbie":      "🔰 Новичок",
    "experienced": "🥉 Опытный",
    "pro":         "🥈 Профи",
    "master":      "🥇 Мастер",
    "admin":       "👑 Админ",
}

# ===== БД =====
def db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    con = db()
    cur = con.cursor()
    # Пользователи
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id      BIGINT PRIMARY KEY,
            username     TEXT,
            full_name    TEXT,
            category     TEXT DEFAULT 'pending',
            nickname     TEXT,
            twitch_url   TEXT,
            balance      REAL DEFAULT 0,
            total_earned REAL DEFAULT 0,
            approved     INTEGER DEFAULT 0,
            rejected     INTEGER DEFAULT 0,
            currency     TEXT DEFAULT 'rub',
            rank         TEXT DEFAULT 'newbie',
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Видео
    cur.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id              SERIAL PRIMARY KEY,
            cutter_id       BIGINT,
            streamer_id     BIGINT,
            file_id         TEXT,
            caption         TEXT,
            status          TEXT DEFAULT 'pending_admin',
            streamer_rating TEXT,
            streamer_reason TEXT,
            submitted_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at     TIMESTAMP
        )
    """)
    # Выводы
    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id         SERIAL PRIMARY KEY,
            user_id    BIGINT,
            amount     REAL,
            symbol     TEXT,
            method     TEXT,
            wallet     TEXT,
            status     TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Назначения стримеров нарезчикам
    cur.execute("""
        CREATE TABLE IF NOT EXISTS assignments (
            id          SERIAL PRIMARY KEY,
            cutter_id   BIGINT,
            streamer_id BIGINT,
            UNIQUE(cutter_id, streamer_id)
        )
    """)
    con.commit()
    con.close()
    ensure_admin()

def ensure_admin():
    con = db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO users (user_id, username, full_name, category, rank)
        VALUES (%s, 'admin', 'Администратор', 'admin', 'admin')
        ON CONFLICT (user_id) DO UPDATE SET category='admin', rank='admin'
    """, (ADMIN_ID,))
    con.commit()
    con.close()

def reg(user_id, username, full_name):
    con = db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO users (user_id, username, full_name)
        VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING
    """, (user_id, username, full_name))
    con.commit()
    con.close()

def get_user(user_id):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    con.close()
    return row

def get_category(user_id):
    u = get_user(user_id)
    return u[3] if u else None  # category is index 3

def set_category(user_id, category):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET category=%s WHERE user_id=%s", (category, user_id))
    con.commit()
    con.close()

def set_nickname(user_id, nickname):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET nickname=%s WHERE user_id=%s", (nickname, user_id))
    con.commit()
    con.close()

def set_twitch(user_id, url):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET twitch_url=%s WHERE user_id=%s", (url, user_id))
    con.commit()
    con.close()

def get_currency(user_id):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT currency FROM users WHERE user_id=%s", (user_id,))
    r = cur.fetchone()
    con.close()
    return r[0] if r else "rub"

def set_currency_convert(user_id, new_cur, category):
    """Конвертирует баланс при смене валюты"""
    con = db()
    cur = con.cursor()
    cur.execute("SELECT balance, currency FROM users WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    if row:
        old_bal, old_cur = row
        if category == "cutter":
            rub_val = old_bal * CUTTER_TO_RUB.get(old_cur, 1)
            new_bal = rub_val / CUTTER_TO_RUB.get(new_cur, 1)
        else:
            rub_val = old_bal * STREAMER_TO_RUB.get(old_cur, 1)
            new_bal = rub_val / STREAMER_TO_RUB.get(new_cur, 1)
        cur.execute("UPDATE users SET balance=%s, currency=%s WHERE user_id=%s", (round(new_bal, 2), new_cur, user_id))
    con.commit()
    con.close()

def add_balance(user_id, amount):
    con = db()
    cur = con.cursor()
    cur.execute("""
        UPDATE users SET balance=balance+%s, total_earned=total_earned+%s, approved=approved+1
        WHERE user_id=%s
    """, (amount, amount, user_id))
    con.commit()
    con.close()

def set_balance(user_id, amount):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET balance=%s WHERE user_id=%s", (amount, user_id))
    con.commit()
    con.close()

def add_balance_manual(user_id, amount):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET balance=balance+%s, total_earned=total_earned+%s WHERE user_id=%s", (amount, amount, user_id))
    con.commit()
    con.close()

def deduct_balance(user_id, amount):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET balance=balance-%s WHERE user_id=%s", (amount, user_id))
    con.commit()
    con.close()

def set_rank(user_id, rank):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE users SET rank=%s WHERE user_id=%s", (rank, user_id))
    con.commit()
    con.close()

def save_video(cutter_id, streamer_id, file_id, caption):
    con = db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO videos (cutter_id, streamer_id, file_id, caption)
        VALUES (%s,%s,%s,%s) RETURNING id
    """, (cutter_id, streamer_id, file_id, caption))
    vid = cur.fetchone()[0]
    con.commit()
    con.close()
    return vid

def get_video(video_id):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT * FROM videos WHERE id=%s", (video_id,))
    r = cur.fetchone()
    con.close()
    return r

def update_video_status(video_id, status, reason=None):
    con = db()
    cur = con.cursor()
    cur.execute("""
        UPDATE videos SET status=%s, streamer_reason=%s, reviewed_at=CURRENT_TIMESTAMP
        WHERE id=%s
    """, (status, reason, video_id))
    if status == "rejected_admin":
        cur.execute("UPDATE users SET rejected=rejected+1 WHERE user_id=(SELECT cutter_id FROM videos WHERE id=%s)", (video_id,))
    con.commit()
    con.close()

def set_video_rating(video_id, rating, reason=None):
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE videos SET streamer_rating=%s, streamer_reason=%s WHERE id=%s", (rating, reason, video_id))
    con.commit()
    con.close()

def save_withdrawal(user_id, amount, symbol, method, wallet):
    con = db()
    cur = con.cursor()
    cur.execute("INSERT INTO withdrawals (user_id, amount, symbol, method, wallet) VALUES (%s,%s,%s,%s,%s)",
                (user_id, amount, symbol, method, wallet))
    con.commit()
    con.close()

def get_all_by_category(category):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT user_id, username, full_name, balance, currency, rank, nickname FROM users WHERE category=%s", (category,))
    rows = cur.fetchall()
    con.close()
    return rows

def get_all_users():
    con = db()
    cur = con.cursor()
    cur.execute("SELECT user_id, username, full_name, balance, currency, rank, category, nickname FROM users ORDER BY created_at DESC")
    rows = cur.fetchall()
    con.close()
    return rows

def assign_streamer(cutter_id, streamer_id):
    con = db()
    cur = con.cursor()
    cur.execute("INSERT INTO assignments (cutter_id, streamer_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (cutter_id, streamer_id))
    con.commit()
    con.close()

def get_assigned_streamers(cutter_id):
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT u.user_id, u.nickname, u.full_name FROM users u
        JOIN assignments a ON u.user_id = a.streamer_id
        WHERE a.cutter_id=%s
    """, (cutter_id,))
    rows = cur.fetchall()
    con.close()
    return rows

def admin_stats():
    con = db()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE category='cutter'"); cutters = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE category='streamer'"); streamers = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE category='pending'"); pending_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM videos WHERE status='pending_admin'"); pending_vids = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM videos WHERE status='approved_admin'"); approved = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'"); withdrawals = cur.fetchone()[0]
    con.close()
    return cutters, streamers, pending_users, pending_vids, approved, withdrawals

# ===== КЛАВИАТУРЫ =====
def main_kb_pending():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Проверить статус", callback_data="check_status")]])

def main_kb_cutter(user_id):
    kb = [
        [InlineKeyboardButton("🎬 Открыть ClipperPay", web_app=WebAppInfo(url="https://clipperpay-miniapp.vercel.app"))],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance"),
         InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("📤 Сдать видео", callback_data="submit_video")],
        [InlineKeyboardButton("💳 Вывод средств", callback_data="withdraw")],
        [InlineKeyboardButton("💱 Валюта", callback_data="currency")],
    ]
    return InlineKeyboardMarkup(kb)

def main_kb_streamer():
    kb = [
        [InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("🔗 Изменить Twitch ссылку", callback_data="change_twitch")],
        [InlineKeyboardButton("💱 Валюта", callback_data="currency")],
    ]
    return InlineKeyboardMarkup(kb)

def main_kb_admin():
    kb = [
        [InlineKeyboardButton("👑 Панель администратора", callback_data="admin_panel")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
    ]
    return InlineKeyboardMarkup(kb)

def back_kb(target="back_to_menu"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data=target)]])

def get_main_kb(user_id, category):
    if category == "admin":
        return main_kb_admin()
    elif category == "cutter":
        return main_kb_cutter(user_id)
    elif category == "streamer":
        return main_kb_streamer()
    else:
        return main_kb_pending()

# ===== СТАРТ =====
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    reg(u.id, u.username, u.full_name)
    if u.id == ADMIN_ID:
        ensure_admin()
        await update.message.reply_text(
            f"👋 Привет, *{u.first_name}*!\n\n👑 Ты администратор ClipperPay.",
            reply_markup=main_kb_admin(), parse_mode="Markdown"
        )
        return

    category = get_category(u.id)

    if category == "pending" or not category:
        await update.message.reply_text(
            f"👋 Привет, *{u.first_name}*!\n\n"
            f"Добро пожаловать в *ClipperPay* 🎬\n\n"
            f"Твой Telegram ID: `{u.id}`\n\n"
            "⏳ Перешли этот ID администратору для активации аккаунта.\n"
            "После активации нажми кнопку ниже.",
            reply_markup=main_kb_pending(), parse_mode="Markdown"
        )
        # Уведомить админа
        try:
            await ctx.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"🆕 *Новый пользователь ожидает активации*\n\n"
                    f"Имя: *{u.full_name}*\n"
                    f"@{u.username or '—'}\n"
                    f"ID: `{u.id}`\n\n"
                    "Назначь категорию в панели администратора."
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Уведомление админа: {e}")
        return

    await send_main_menu(update.message, u.id, category, u.first_name)

async def send_main_menu(msg, user_id, category, name=None):
    if category == "cutter":
        text = f"📋 Главное меню, *{name or ''}*:" if name else "📋 Главное меню:"
        await msg.reply_text(text, reply_markup=main_kb_cutter(user_id), parse_mode="Markdown")
    elif category == "streamer":
        text = f"📋 Главное меню, *{name or ''}*:" if name else "📋 Главное меню:"
        await msg.reply_text(text, reply_markup=main_kb_streamer(), parse_mode="Markdown")
    elif category == "admin":
        await msg.reply_text("📋 Панель:", reply_markup=main_kb_admin())

async def menu_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    cat = get_category(u.id)
    if not cat or cat == "pending":
        await update.message.reply_text(
            f"⏳ Твой ID: `{u.id}`\nОжидай активации администратора.",
            reply_markup=main_kb_pending(), parse_mode="Markdown"
        )
        return
    await send_main_menu(update.message, u.id, cat)

# ===== ГЛАВНЫЙ ОБРАБОТЧИК КНОПОК =====
async def btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data
    cat = get_category(uid)

    # --- ПРОВЕРИТЬ СТАТУС ---
    if data == "check_status":
        cat = get_category(uid)
        if cat and cat != "pending":
            if cat == "cutter":
                # Проверяем нужно ли указать никнейм
                u = get_user(uid)
                if not u[4]:  # nickname
                    await q.edit_message_text(
                        "✅ Аккаунт активирован как *Нарезчик*!\n\nВведи свой никнейм:",
                        parse_mode="Markdown"
                    )
                    return ST_WAIT_NICKNAME
                await q.edit_message_text(
                    "✅ Аккаунт активирован как *Нарезчик*!",
                    reply_markup=main_kb_cutter(uid), parse_mode="Markdown"
                )
            elif cat == "streamer":
                u = get_user(uid)
                if not u[5]:  # twitch_url
                    await q.edit_message_text(
                        "✅ Аккаунт активирован как *Стример*!\n\nВведи свою Twitch ссылку:",
                        parse_mode="Markdown"
                    )
                    return ST_STREAMER_LINK
                await q.edit_message_text(
                    "✅ Аккаунт активирован!",
                    reply_markup=main_kb_streamer(), parse_mode="Markdown"
                )
        else:
            await q.edit_message_text(
                f"⏳ Твой ID: `{q.from_user.id}`\nАккаунт ещё не активирован. Подожди.",
                reply_markup=main_kb_pending(), parse_mode="Markdown"
            )
        return

    # --- НАЗАД В МЕНЮ ---
    if data == "back_to_menu":
        ctx.user_data.clear()
        kb = get_main_kb(uid, cat)
        await q.edit_message_text("📋 Главное меню:", reply_markup=kb)
        return

    # --- БАЛАНС ---
    if data == "balance":
        u = get_user(uid)
        if u:
            bal = u[7]  # balance
            total = u[8]  # total_earned
            appr = u[9]
            rej = u[10]
            cur = u[11]  # currency
            ci = CUTTER_CUR.get(cur, CUTTER_CUR["rub"])
            sym = ci["symbol"]
            reward = ci["reward"]
            text = (
                "💰 *Ваш баланс*\n\n"
                f"💵 Заработано: *{bal:.2f}{sym}*\n"
                f"📈 За всё время: *{total:.2f}{sym}*\n"
                f"💵 Фикса: *{reward}{sym}* за видео\n\n"
                f"✅ Одобрено: *{appr}*\n"
                f"❌ Отклонено: *{rej}*"
            )
        else:
            text = "❌ Ошибка"
        await q.edit_message_text(text, reply_markup=back_kb(), parse_mode="Markdown")

    # --- ПРОФИЛЬ ---
    elif data == "profile":
        u = get_user(uid)
        if u:
            cat_u = u[3]
            nick = u[4]
            bal = u[7]
            total = u[8]
            appr = u[9]
            rej = u[10]
            cur = u[11]
            rank = u[12]
            rank_label = RANKS.get(rank, "🔰 Новичок")

            if cat_u == "cutter":
                ci = CUTTER_CUR.get(cur, CUTTER_CUR["rub"])
                sym = ci["symbol"]
                reward = ci["reward"]
                text = (
                    f"👤 *Профиль — Нарезчик*\n\n"
                    f"Никнейм: *{nick or '—'}*\n"
                    f"ID: `{uid}`\n"
                    f"Ранг: {rank_label}\n\n"
                    f"💰 Баланс: *{bal:.2f}{sym}*\n"
                    f"📈 За всё время: *{total:.2f}{sym}*\n"
                    f"💵 Фикса: *{reward}{sym}* за видео\n\n"
                    f"✅ Одобрено: *{appr}* | ❌ Отклонено: *{rej}*"
                )
            elif cat_u == "streamer":
                ci = STREAMER_CUR.get(cur, STREAMER_CUR["rub"])
                sym = ci["symbol"]
                price = ci["price"]
                debt = bal  # у стримера баланс = долг
                text = (
                    f"👤 *Профиль — Стример*\n\n"
                    f"ID: `{uid}`\n\n"
                    f"💸 К оплате: *{debt:.2f}{sym}*\n"
                    f"💵 Цена за видео: *{price}{sym}*\n\n"
                    f"✅ Видео одобрено: *{appr}*"
                )
            else:
                text = f"👤 *Профиль*\n\nID: `{uid}`"
        else:
            text = "❌ Ошибка"
        await q.edit_message_text(text, reply_markup=back_kb(), parse_mode="Markdown")

    # --- ВАЛЮТА ---
    elif data == "currency":
        if cat == "streamer":
            cur_dict = STREAMER_CUR
        else:
            cur_dict = CUTTER_CUR
        text = "💱 *Выбор валюты*\n\nВыбери валюту (баланс будет конвертирован):"
        kb = [[InlineKeyboardButton(ci["name"], callback_data=f"setcur_{code}")] for code, ci in cur_dict.items()]
        kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_menu")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    elif data.startswith("setcur_"):
        code = data.split("_")[1]
        set_currency_convert(uid, code, cat)
        if cat == "streamer":
            ci = STREAMER_CUR.get(code, STREAMER_CUR["rub"])
        else:
            ci = CUTTER_CUR.get(code, CUTTER_CUR["rub"])
        await q.edit_message_text(
            f"✅ Валюта изменена на *{ci['name']}*",
            reply_markup=back_kb(), parse_mode="Markdown"
        )

    # --- ИЗМЕНИТЬ TWITCH ---
    elif data == "change_twitch":
        await q.edit_message_text(
            "🔗 Введи новую Twitch ссылку:\n\n_(например: https://twitch.tv/имя)_",
            reply_markup=back_kb(), parse_mode="Markdown"
        )
        return ST_STREAMER_LINK

    # --- ВЫВОД СРЕДСТВ ---
    elif data == "withdraw":
        u = get_user(uid)
        if u:
            bal = u[7]
            cur = u[11]
            ci = CUTTER_CUR.get(cur, CUTTER_CUR["rub"])
            sym = ci["symbol"]
            ctx.user_data["withdraw_symbol"] = sym
            ctx.user_data["withdraw_amount"] = bal
            text = (
                f"💳 *Вывод средств*\n\n"
                f"Баланс: *{bal:.2f}{sym}*\n\n"
                "Перевод только криптой по сети *USDT ERC-20*.\n\n"
                "Введи адрес кошелька:"
            )
            await q.edit_message_text(text, reply_markup=back_kb(), parse_mode="Markdown")
            return ST_WALLET
        await q.edit_message_text("❌ Ошибка", reply_markup=back_kb())

    # --- ОДОБРИТЬ ВИДЕО (АДМИН) ---
    elif data.startswith("approve_"):
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        video_id = int(data.split("_")[1])
        video = get_video(video_id)
        if not video:
            await q.answer("Видео не найдено")
            return
        cutter_id = video[1]
        streamer_id = video[2]
        update_video_status(video_id, "approved_admin")

        # Отправить стримеру
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("👍 Хорошо", callback_data=f"rate_good_{video_id}"),
            InlineKeyboardButton("👎 Не очень", callback_data=f"rate_bad_{video_id}"),
        ]])
        try:
            await ctx.bot.send_video(
                chat_id=streamer_id,
                video=video[3],  # file_id
                caption=f"🎬 *Новая нарезка #{video_id}*\n\nПоставь оценку:",
                reply_markup=kb, parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Отправка стримеру: {e}")

        await q.edit_message_caption(
            caption=f"✅ *Видео #{video_id} одобрено* — отправлено стримеру",
            parse_mode="Markdown"
        )

    # --- ОТКАЗАТЬ ВИДЕО (АДМИН) ---
    elif data.startswith("reject_"):
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        video_id = int(data.split("_")[1])
        ctx.user_data["review_video_id"] = video_id
        await q.edit_message_caption(
            caption=f"❌ *Отказ по видео #{video_id}*\n\nНапиши причину:",
            parse_mode="Markdown"
        )
        return ST_REJECT_REASON

    # --- ОЦЕНКА СТРИМЕРА ---
    elif data.startswith("rate_good_"):
        video_id = int(data.split("_")[2])
        video = get_video(video_id)
        if not video:
            return
        cutter_id = video[1]
        set_video_rating(video_id, "good")
        update_video_status(video_id, "completed")

        # Начислить нарезчику
        cur = get_currency(cutter_id)
        ci = CUTTER_CUR.get(cur, CUTTER_CUR["rub"])
        reward = ci["reward"]
        sym = ci["symbol"]
        add_balance(cutter_id, reward)

        # Начислить стримеру долг
        streamer_id = video[2]
        s_cur = get_currency(streamer_id)
        sci = STREAMER_CUR.get(s_cur, STREAMER_CUR["rub"])
        add_balance_manual(streamer_id, sci["price"])

        await q.edit_message_caption(
            caption=f"👍 Спасибо за оценку! Нарезчику начислено *{reward}{sym}*",
            parse_mode="Markdown"
        )
        try:
            await ctx.bot.send_message(
                chat_id=cutter_id,
                text=f"🎉 *Видео #{video_id} одобрено стримером!*\n\n💰 Начислено: *+{reward}{sym}*",
                parse_mode="Markdown"
            )
            await ctx.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"✅ Стример одобрил видео #{video_id}. Нарезчику `{cutter_id}` начислено *+{reward}{sym}*",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Уведомление: {e}")

    elif data.startswith("rate_bad_"):
        video_id = int(data.split("_")[2])
        ctx.user_data["rating_video_id"] = video_id
        await q.edit_message_caption(
            caption="👎 *Опиши что не понравилось:*",
            parse_mode="Markdown"
        )
        return ST_STREAMER_BAD_REASON

    # --- ПЕРЕСЛАТЬ ПРАВКИ НАРЕЗЧИКУ (АДМИН) ---
    elif data.startswith("forward_"):
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        video_id = int(data.split("_")[1])
        video = get_video(video_id)
        if not video:
            return
        cutter_id = video[1]
        reason = video[6]  # streamer_reason
        try:
            await ctx.bot.send_message(
                chat_id=cutter_id,
                text=(
                    f"✏️ *Правки по видео #{video_id}*\n\n"
                    f"📝 *Стример написал:*\n{reason}\n\n"
                    "Исправь и сдай заново."
                ),
                parse_mode="Markdown"
            )
            await q.edit_message_text(f"✅ Правки переслали нарезчику `{cutter_id}`", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Пересылка правок: {e}")

    # ===== ПАНЕЛЬ АДМИНИСТРАТОРА =====
    elif data == "admin_panel":
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        kb = [
            [InlineKeyboardButton("👥 Нарезчики", callback_data="admin_cutters"),
             InlineKeyboardButton("🎮 Стримеры", callback_data="admin_streamers")],
            [InlineKeyboardButton("⏳ Ожидают активации", callback_data="admin_pending")],
            [InlineKeyboardButton("🔧 Управление участником", callback_data="admin_manage")],
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_menu")],
        ]
        await q.edit_message_text("👑 *Панель администратора*", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    elif data == "admin_stats":
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        cutters, streamers, pending_u, pending_v, approved, withdrawals = admin_stats()
        text = (
            f"📊 *Статистика ClipperPay*\n\n"
            f"✂️ Нарезчиков: *{cutters}*\n"
            f"🎮 Стримеров: *{streamers}*\n"
            f"⏳ Ожидают активации: *{pending_u}*\n\n"
            f"📹 Видео на проверке: *{pending_v}*\n"
            f"✅ Одобрено всего: *{approved}*\n"
            f"💸 Заявок на вывод: *{withdrawals}*"
        )
        await q.edit_message_text(text, reply_markup=back_kb("admin_panel"), parse_mode="Markdown")

    elif data == "admin_cutters":
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        users = get_all_by_category("cutter")
        text = "✂️ *Нарезчики:*\n\n"
        if not users:
            text += "Нет нарезчиков."
        for u in users:
            u_id, uname, fname, bal, cur, rank, nick = u
            sym = CUTTER_CUR.get(cur, CUTTER_CUR["rub"])["symbol"]
            rl = RANKS.get(rank, "🔰")
            text += f"• *{nick or fname}* (@{uname or '—'})\n  ID: `{u_id}` | {bal:.2f}{sym} | {rl}\n\n"
        await q.edit_message_text(text, reply_markup=back_kb("admin_panel"), parse_mode="Markdown")

    elif data == "admin_streamers":
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        users = get_all_by_category("streamer")
        text = "🎮 *Стримеры:*\n\n"
        if not users:
            text += "Нет стримеров."
        for u in users:
            u_id, uname, fname, bal, cur, rank, nick = u
            sym = STREAMER_CUR.get(cur, STREAMER_CUR["rub"])["symbol"]
            text += f"• Кличка: *{nick or '—'}* (@{uname or '—'})\n  ID: `{u_id}` | Долг: {bal:.2f}{sym}\n\n"
        await q.edit_message_text(text, reply_markup=back_kb("admin_panel"), parse_mode="Markdown")

    elif data == "admin_pending":
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        users = get_all_by_category("pending")
        text = "⏳ *Ожидают активации:*\n\n"
        if not users:
            text += "Никто не ждёт."
        for u in users:
            u_id, uname, fname = u[0], u[1], u[2]
            text += f"• *{fname}* (@{uname or '—'}) — ID: `{u_id}`\n"
        await q.edit_message_text(text, reply_markup=back_kb("admin_panel"), parse_mode="Markdown")

    elif data == "admin_manage":
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        ctx.user_data["admin_action"] = "manage"
        await q.edit_message_text(
            "🔧 Введи Telegram ID участника:",
            reply_markup=back_kb("admin_panel"), parse_mode="Markdown"
        )
        return ST_ADMIN_USER_ID

    elif data.startswith("amng_"):
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        target_id = int(data.split("_")[1])
        ctx.user_data["target_id"] = target_id
        u = get_user(target_id)
        if not u:
            await q.edit_message_text("❌ Пользователь не найден.", reply_markup=back_kb("admin_panel"))
            return
        cat_t = u[3]
        nick = u[4]
        bal = u[7]
        cur = u[11]
        rank = u[12]
        if cat_t == "streamer":
            sym = STREAMER_CUR.get(cur, STREAMER_CUR["rub"])["symbol"]
        else:
            sym = CUTTER_CUR.get(cur, CUTTER_CUR["rub"])["symbol"]
        rl = RANKS.get(rank, "🔰 Новичок")
        text = (
            f"⚙️ *Управление*\n\n"
            f"ID: `{target_id}`\n"
            f"Категория: *{cat_t}*\n"
            f"Никнейм/кличка: *{nick or '—'}*\n"
            f"Баланс: *{bal:.2f}{sym}*\n"
            f"Ранг: {rl}"
        )
        kb = [
            [InlineKeyboardButton("📂 Назначить категорию", callback_data=f"acat_{target_id}")],
            [InlineKeyboardButton("💰 Установить баланс", callback_data=f"asetbal_{target_id}"),
             InlineKeyboardButton("➕ Добавить баланс", callback_data=f"aaddbal_{target_id}")],
            [InlineKeyboardButton("🏅 Изменить ранг", callback_data=f"arank_{target_id}")],
            [InlineKeyboardButton("✏️ Задать кличку/никнейм", callback_data=f"anick_{target_id}")],
            [InlineKeyboardButton("🔗 Назначить стримера нарезчику", callback_data=f"aassign_{target_id}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="admin_panel")],
        ]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    elif data.startswith("acat_"):
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        target_id = int(data.split("_")[1])
        ctx.user_data["target_id"] = target_id
        kb = [
            [InlineKeyboardButton("✂️ Нарезчик", callback_data=f"docat_{target_id}_cutter")],
            [InlineKeyboardButton("🎮 Стример", callback_data=f"docat_{target_id}_streamer")],
            [InlineKeyboardButton("⬅️ Отмена", callback_data=f"amng_{target_id}")],
        ]
        await q.edit_message_text(
            f"📂 Выбери категорию для `{target_id}`:",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
        )

    elif data.startswith("docat_"):
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        parts = data.split("_")
        target_id = int(parts[1])
        new_cat = parts[2]
        set_category(target_id, new_cat)
        cat_name = "✂️ Нарезчик" if new_cat == "cutter" else "🎮 Стример"
        await q.edit_message_text(
            f"✅ Категория `{target_id}` изменена на *{cat_name}*",
            reply_markup=back_kb("admin_panel"), parse_mode="Markdown"
        )
        try:
            if new_cat == "cutter":
                msg = "✅ Твой аккаунт активирован как *Нарезчик*!\n\nВведи свой никнейм:"
                await ctx.bot.send_message(chat_id=target_id, text=msg, parse_mode="Markdown")
            elif new_cat == "streamer":
                msg = "✅ Твой аккаунт активирован как *Стример*!\n\nВведи свою Twitch ссылку:"
                await ctx.bot.send_message(chat_id=target_id, text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Уведомление пользователю: {e}")

    elif data.startswith("asetbal_"):
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        target_id = int(data.split("_")[1])
        ctx.user_data["target_id"] = target_id
        ctx.user_data["admin_action"] = "set_balance"
        await q.edit_message_text(
            f"💰 Введи новый баланс для `{target_id}`:",
            reply_markup=back_kb("admin_panel"), parse_mode="Markdown"
        )
        return ST_ADMIN_SET_BAL

    elif data.startswith("aaddbal_"):
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        target_id = int(data.split("_")[1])
        ctx.user_data["target_id"] = target_id
        ctx.user_data["admin_action"] = "add_balance"
        await q.edit_message_text(
            f"➕ Введи сумму для пополнения `{target_id}`:",
            reply_markup=back_kb("admin_panel"), parse_mode="Markdown"
        )
        return ST_ADMIN_ADD_BAL

    elif data.startswith("arank_"):
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        target_id = int(data.split("_")[1])
        kb = [
            [InlineKeyboardButton("🔰 Новичок", callback_data=f"dorank_{target_id}_newbie")],
            [InlineKeyboardButton("🥉 Опытный", callback_data=f"dorank_{target_id}_experienced")],
            [InlineKeyboardButton("🥈 Профи", callback_data=f"dorank_{target_id}_pro")],
            [InlineKeyboardButton("🥇 Мастер", callback_data=f"dorank_{target_id}_master")],
            [InlineKeyboardButton("⬅️ Отмена", callback_data=f"amng_{target_id}")],
        ]
        await q.edit_message_text(
            f"🏅 Выбери ранг для `{target_id}`:",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
        )

    elif data.startswith("dorank_"):
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        parts = data.split("_")
        target_id = int(parts[1])
        new_rank = parts[2]
        set_rank(target_id, new_rank)
        rl = RANKS.get(new_rank, "🔰 Новичок")
        await q.edit_message_text(
            f"✅ Ранг `{target_id}` → *{rl}*",
            reply_markup=back_kb("admin_panel"), parse_mode="Markdown"
        )
        try:
            await ctx.bot.send_message(chat_id=target_id, text=f"🏅 Твой ранг изменён на *{rl}*!", parse_mode="Markdown")
        except: pass

    elif data.startswith("anick_"):
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        target_id = int(data.split("_")[1])
        ctx.user_data["target_id"] = target_id
        ctx.user_data["admin_action"] = "set_nickname"
        await q.edit_message_text(
            f"✏️ Введи кличку/никнейм для `{target_id}`:",
            reply_markup=back_kb("admin_panel"), parse_mode="Markdown"
        )
        return ST_ADMIN_NICKNAME

    elif data.startswith("aassign_"):
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        target_id = int(data.split("_")[1])
        ctx.user_data["target_id"] = target_id
        ctx.user_data["admin_action"] = "assign"
        await q.edit_message_text(
            f"🔗 Введи Telegram ID стримера для назначения нарезчику `{target_id}`:",
            reply_markup=back_kb("admin_panel"), parse_mode="Markdown"
        )
        return ST_ADMIN_ASSIGN

    elif data == "admin_broadcast":
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        kb = [
            [InlineKeyboardButton("✂️ Нарезчикам", callback_data="broadcast_cutter")],
            [InlineKeyboardButton("🎮 Стримерам", callback_data="broadcast_streamer")],
            [InlineKeyboardButton("👥 Всем", callback_data="broadcast_all")],
            [InlineKeyboardButton("⬅️ Отмена", callback_data="admin_panel")],
        ]
        await q.edit_message_text("📢 Кому отправить рассылку?", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("broadcast_"):
        if uid != ADMIN_ID:
            await q.answer("❌ Нет доступа", show_alert=True)
            return
        target = data.split("_")[1]
        ctx.user_data["broadcast_target"] = target
        await q.edit_message_text(
            "📢 Напиши текст сообщения для рассылки:",
            reply_markup=back_kb("admin_panel")
        )
        return ST_ADMIN_BROADCAST


# ===== СДАЧА ВИДЕО (НАРЕЗЧИК) =====
async def submit_video_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    streamers = get_assigned_streamers(uid)
    if not streamers:
        await q.edit_message_text(
            "❌ Тебе ещё не назначены стримеры. Обратись к администратору.",
            reply_markup=back_kb()
        )
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(s[1] or s[2], callback_data=f"pick_streamer_{s[0]}")] for s in streamers]
    kb.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_submit")])
    await q.edit_message_text(
        "📤 *Сдача видео*\n\nВыбери стримера:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )
    return ST_CUTTER_VIDEO

async def pick_streamer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    streamer_id = int(q.data.split("_")[2])
    ctx.user_data["streamer_id"] = streamer_id

    # Получить кличку стримера
    u = get_user(streamer_id)
    nick = u[4] if u else "Стример"

    await q.edit_message_text(
        f"📤 Стример: *{nick}*\n\n"
        "⚠️ *Требования:*\n"
        "• Длительность: от 15 до 25 секунд\n"
        "• Субтитры — обязательно\n"
        "• Хорошее качество\n\n"
        "💡 *Совет:* Используй приложение *Wink* для монтажа!\n\n"
        "Отправь видео:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel_submit")]]),
        parse_mode="Markdown"
    )
    return ST_CUTTER_VIDEO

async def cancel_submit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data.clear()
    uid = q.from_user.id
    await q.edit_message_text("📋 Главное меню:", reply_markup=main_kb_cutter(uid))
    return ConversationHandler.END

async def got_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.message.video:
        file_id = update.message.video.file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    else:
        await update.message.reply_text("⚠️ Отправь видео файл.")
        return ST_CUTTER_VIDEO

    streamer_id = ctx.user_data.get("streamer_id")
    if not streamer_id:
        await update.message.reply_text("❌ Ошибка. Начни сначала.")
        return ConversationHandler.END

    caption = update.message.caption or ""
    video_id = save_video(user.id, streamer_id, file_id, caption)
    ctx.user_data.clear()

    await update.message.reply_text(
        f"✅ *Видео #{video_id} отправлено на проверку!*\n\nОжидай уведомления.",
        parse_mode="Markdown", reply_markup=main_kb_cutter(user.id)
    )

    # Получить кличку стримера
    su = get_user(streamer_id)
    s_nick = su[4] if su else "—"
    cutter_u = get_user(user.id)
    c_nick = cutter_u[4] if cutter_u else user.username or "—"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{video_id}"),
        InlineKeyboardButton("❌ Отказать", callback_data=f"reject_{video_id}"),
    ]])
    try:
        await ctx.bot.send_video(
            chat_id=ADMIN_ID, video=file_id,
            caption=(
                f"📥 *Видео #{video_id} на проверку*\n\n"
                f"Нарезчик: *{c_nick}* (ID: `{user.id}`)\n"
                f"Стример: *{s_nick}* (ID: `{streamer_id}`)"
            ),
            reply_markup=kb, parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Отправка админу: {e}")
    return ConversationHandler.END


# ===== ОБРАБОТЧИКИ ТЕКСТА =====
async def got_reject_reason(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    video_id = ctx.user_data.get("review_video_id")
    reason = update.message.text
    video = get_video(video_id)
    if not video:
        await update.message.reply_text("❌ Видео не найдено.")
        return ConversationHandler.END
    cutter_id = video[1]
    update_video_status(video_id, "rejected_admin", reason)
    ctx.user_data.clear()
    await update.message.reply_text(f"✅ Видео #{video_id} отклонено.")
    try:
        await ctx.bot.send_message(
            chat_id=cutter_id,
            text=f"❌ *Видео #{video_id} отклонено*\n\n📝 *Причина:*\n{reason}",
            parse_mode="Markdown", reply_markup=main_kb_cutter(cutter_id)
        )
    except Exception as e:
        logger.error(f"Уведомление: {e}")
    return ConversationHandler.END

async def got_streamer_bad_reason(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    video_id = ctx.user_data.get("rating_video_id")
    reason = update.message.text
    set_video_rating(video_id, "bad", reason)
    update_video_status(video_id, "bad_rating")
    ctx.user_data.clear()
    await update.message.reply_text(
        "📝 Спасибо за отзыв! Администратор получит уведомление.",
        reply_markup=main_kb_streamer()
    )
    try:
        video = get_video(video_id)
        cutter_id = video[1] if video else None
        await ctx.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"👎 *Стример дал плохую оценку видео #{video_id}*\n\n"
                f"Стример ID: `{uid}`\n"
                f"Нарезчик ID: `{cutter_id}`\n\n"
                f"📝 *Причина:*\n{reason}"
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📨 Переслать нарезчику", callback_data=f"forward_{video_id}")
            ]]),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Уведомление admin: {e}")
    return ConversationHandler.END

async def got_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    wallet = update.message.text.strip()
    sym = ctx.user_data.get("withdraw_symbol", "₽")
    amount = ctx.user_data.get("withdraw_amount", 0)
    ctx.user_data.clear()
    save_withdrawal(uid, amount, sym, "USDT ERC-20", wallet)
    deduct_balance(uid, amount)
    await update.message.reply_text(
        f"✅ *Заявка на вывод создана!*\n\n"
        f"💰 Сумма: *{amount:.2f}{sym}*\n"
        f"🔑 Кошелёк: `{wallet}`\n\n"
        "Выплата в течение 24 часов.",
        parse_mode="Markdown", reply_markup=main_kb_cutter(uid)
    )
    u = update.effective_user
    try:
        await ctx.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"💸 *Заявка на вывод*\n\n"
                f"От: @{u.username or '—'} ({u.full_name})\n"
                f"ID: `{uid}`\n"
                f"Сумма: *{amount:.2f}{sym}*\n"
                f"Кошелёк: `{wallet}`"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Уведомление: {e}")
    return ConversationHandler.END

async def got_nickname(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    nick = update.message.text.strip()
    set_nickname(uid, nick)
    await update.message.reply_text(
        f"✅ Никнейм сохранён: *{nick}*\n\nВыбери валюту:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🇷🇺 Рубль (₽)", callback_data="setcur_rub")],
            [InlineKeyboardButton("🇺🇦 Гривна (₴)", callback_data="setcur_uah")],
            [InlineKeyboardButton("🇺🇸 Доллар ($)", callback_data="setcur_usd")],
            [InlineKeyboardButton("🇪🇺 Евро (€)", callback_data="setcur_eur")],
        ]),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def got_twitch_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    link = update.message.text.strip()
    if not re.match(r'https?://(www\.)?twitch\.tv/.+', link):
        await update.message.reply_text(
            "❌ Это не Twitch ссылка. Введи ссылку вида:\n`https://twitch.tv/имя`",
            parse_mode="Markdown"
        )
        return ST_STREAMER_LINK
    set_twitch(uid, link)
    await update.message.reply_text(
        f"✅ Twitch ссылка сохранена!\n\nВыбери валюту:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🇷🇺 Рубль (₽)", callback_data="setcur_rub")],
            [InlineKeyboardButton("🇺🇦 Гривна (₴)", callback_data="setcur_uah")],
            [InlineKeyboardButton("🇺🇸 Доллар ($)", callback_data="setcur_usd")],
            [InlineKeyboardButton("🇪🇺 Евро (€)", callback_data="setcur_eur")],
        ]),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def got_admin_user_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        target_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введи числовой ID.")
        return ST_ADMIN_USER_ID
    u = get_user(target_id)
    if not u:
        await update.message.reply_text("❌ Пользователь не найден.")
        return ConversationHandler.END
    cat_t = u[3]
    nick = u[4]
    bal = u[7]
    cur = u[11]
    rank = u[12]
    if cat_t == "streamer":
        sym = STREAMER_CUR.get(cur, STREAMER_CUR["rub"])["symbol"]
    else:
        sym = CUTTER_CUR.get(cur, CUTTER_CUR["rub"])["symbol"]
    rl = RANKS.get(rank, "🔰 Новичок")
    text = (
        f"⚙️ *Управление*\n\n"
        f"ID: `{target_id}`\n"
        f"Категория: *{cat_t}*\n"
        f"Никнейм: *{nick or '—'}*\n"
        f"Баланс: *{bal:.2f}{sym}*\n"
        f"Ранг: {rl}"
    )
    kb = [
        [InlineKeyboardButton("📂 Назначить категорию", callback_data=f"acat_{target_id}")],
        [InlineKeyboardButton("💰 Установить баланс", callback_data=f"asetbal_{target_id}"),
         InlineKeyboardButton("➕ Добавить баланс", callback_data=f"aaddbal_{target_id}")],
        [InlineKeyboardButton("🏅 Изменить ранг", callback_data=f"arank_{target_id}")],
        [InlineKeyboardButton("✏️ Задать кличку", callback_data=f"anick_{target_id}")],
        [InlineKeyboardButton("🔗 Назначить стримера", callback_data=f"aassign_{target_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="admin_panel")],
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return ConversationHandler.END

async def got_set_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        amount = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введи число.")
        return ST_ADMIN_SET_BAL
    target_id = ctx.user_data.get("target_id")
    set_balance(target_id, amount)
    u = get_user(target_id)
    cur = u[11] if u else "rub"
    cat_t = u[3] if u else "cutter"
    sym = STREAMER_CUR.get(cur, STREAMER_CUR["rub"])["symbol"] if cat_t == "streamer" else CUTTER_CUR.get(cur, CUTTER_CUR["rub"])["symbol"]
    ctx.user_data.clear()
    await update.message.reply_text(f"✅ Баланс `{target_id}` = *{amount:.2f}{sym}*", parse_mode="Markdown")
    try:
        await ctx.bot.send_message(chat_id=target_id, text=f"💰 Твой баланс изменён: *{amount:.2f}{sym}*", parse_mode="Markdown")
    except: pass
    return ConversationHandler.END

async def got_add_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        amount = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введи число.")
        return ST_ADMIN_ADD_BAL
    target_id = ctx.user_data.get("target_id")
    add_balance_manual(target_id, amount)
    u = get_user(target_id)
    cur = u[11] if u else "rub"
    cat_t = u[3] if u else "cutter"
    sym = STREAMER_CUR.get(cur, STREAMER_CUR["rub"])["symbol"] if cat_t == "streamer" else CUTTER_CUR.get(cur, CUTTER_CUR["rub"])["symbol"]
    ctx.user_data.clear()
    await update.message.reply_text(f"✅ Добавлено `{target_id}`: *+{amount:.2f}{sym}*", parse_mode="Markdown")
    try:
        await ctx.bot.send_message(chat_id=target_id, text=f"💰 Баланс пополнен на *+{amount:.2f}{sym}*!", parse_mode="Markdown")
    except: pass
    return ConversationHandler.END

async def got_admin_nickname(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    target_id = ctx.user_data.get("target_id")
    nick = update.message.text.strip()
    set_nickname(target_id, nick)
    ctx.user_data.clear()
    await update.message.reply_text(f"✅ Кличка/никнейм для `{target_id}` = *{nick}*", parse_mode="Markdown")
    return ConversationHandler.END

async def got_admin_assign(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        streamer_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введи числовой ID стримера.")
        return ST_ADMIN_ASSIGN
    cutter_id = ctx.user_data.get("target_id")
    su = get_user(streamer_id)
    if not su or su[3] != "streamer":
        await update.message.reply_text("❌ Пользователь не найден или не является стримером.")
        return ConversationHandler.END
    assign_streamer(cutter_id, streamer_id)
    nick = su[4] or su[2]
    ctx.user_data.clear()
    await update.message.reply_text(
        f"✅ Стример *{nick}* назначен нарезчику `{cutter_id}`",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def got_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    target = ctx.user_data.get("broadcast_target", "all")
    text = update.message.text
    ctx.user_data.clear()
    if target == "all":
        users = get_all_users()
        ids = [u[0] for u in users if u[0] != ADMIN_ID]
    else:
        users = get_all_by_category(target)
        ids = [u[0] for u in users]
    sent = 0
    for uid in ids:
        try:
            await ctx.bot.send_message(chat_id=uid, text=f"📢 *Сообщение от администратора:*\n\n{text}", parse_mode="Markdown")
            sent += 1
        except: pass
    await update.message.reply_text(f"✅ Рассылка отправлена *{sent}* пользователям.", parse_mode="Markdown")
    return ConversationHandler.END


# ===== ЗАПУСК =====
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Сдача видео
    video_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(submit_video_start, pattern="^submit_video$")],
        states={
            ST_CUTTER_VIDEO: [
                CallbackQueryHandler(pick_streamer, pattern="^pick_streamer_"),
                MessageHandler(filters.VIDEO | filters.Document.VIDEO, got_video),
                CallbackQueryHandler(cancel_submit, pattern="^cancel_submit$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", menu_cmd), CallbackQueryHandler(cancel_submit, pattern="^cancel_submit$")],
        per_user=True, per_chat=True, allow_reentry=True,
    )

    # Отказ видео
    reject_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(btn, pattern="^reject_")],
        states={
            ST_REJECT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_reject_reason)],
        },
        fallbacks=[CommandHandler("cancel", menu_cmd)],
        per_user=True, per_chat=False,
    )

    # Оценка стримера
    rating_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(btn, pattern="^rate_bad_")],
        states={
            ST_STREAMER_BAD_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_streamer_bad_reason)],
        },
        fallbacks=[CommandHandler("cancel", menu_cmd)],
        per_user=True, per_chat=True,
    )

    # Вывод средств
    withdraw_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(btn, pattern="^withdraw$")],
        states={
            ST_WALLET: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_wallet)],
        },
        fallbacks=[CommandHandler("cancel", menu_cmd), CallbackQueryHandler(btn, pattern="^back_to_menu$")],
        per_user=True, per_chat=True,
    )

    # Никнейм нарезчика
    nickname_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(btn, pattern="^check_status$")],
        states={
            ST_WAIT_NICKNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_nickname)],
            ST_STREAMER_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_twitch_link)],
        },
        fallbacks=[CommandHandler("cancel", menu_cmd)],
        per_user=True, per_chat=True, allow_reentry=True,
    )

    # Twitch ссылка
    twitch_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(btn, pattern="^change_twitch$")],
        states={
            ST_STREAMER_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_twitch_link)],
        },
        fallbacks=[CommandHandler("cancel", menu_cmd)],
        per_user=True, per_chat=True,
    )

    # Управление участником
    admin_manage_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(btn, pattern="^admin_manage$")],
        states={
            ST_ADMIN_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_admin_user_id)],
            ST_ADMIN_SET_BAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_set_balance)],
            ST_ADMIN_ADD_BAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_add_balance)],
        },
        fallbacks=[CommandHandler("cancel", menu_cmd)],
        per_user=True, per_chat=False,
    )

    # Баланс через кнопки
    bal_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(btn, pattern="^asetbal_"),
            CallbackQueryHandler(btn, pattern="^aaddbal_"),
        ],
        states={
            ST_ADMIN_SET_BAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_set_balance)],
            ST_ADMIN_ADD_BAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_add_balance)],
        },
        fallbacks=[CommandHandler("cancel", menu_cmd)],
        per_user=True, per_chat=False,
    )

    # Кличка через кнопку
    nick_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(btn, pattern="^anick_")],
        states={
            ST_ADMIN_NICKNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_admin_nickname)],
        },
        fallbacks=[CommandHandler("cancel", menu_cmd)],
        per_user=True, per_chat=False,
    )

    # Назначение стримера
    assign_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(btn, pattern="^aassign_")],
        states={
            ST_ADMIN_ASSIGN: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_admin_assign)],
        },
        fallbacks=[CommandHandler("cancel", menu_cmd)],
        per_user=True, per_chat=False,
    )

    # Рассылка
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(btn, pattern="^broadcast_")],
        states={
            ST_ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_broadcast)],
        },
        fallbacks=[CommandHandler("cancel", menu_cmd)],
        per_user=True, per_chat=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(video_conv)
    app.add_handler(reject_conv)
    app.add_handler(rating_conv)
    app.add_handler(withdraw_conv)
    app.add_handler(nickname_conv)
    app.add_handler(twitch_conv)
    app.add_handler(admin_manage_conv)
    app.add_handler(bal_conv)
    app.add_handler(nick_conv)
    app.add_handler(assign_conv)
    app.add_handler(broadcast_conv)
    app.add_handler(CallbackQueryHandler(btn))

    print("✅ ClipperPay Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
