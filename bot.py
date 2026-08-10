import telebot
from telebot import types
import sqlite3
import math
import datetime
import pandas as pd 
import os 

TOKEN = "8302741817:AAG8nkI43v5_ctnU872GBenz5u2JsdMy5os"
bot = telebot.TeleBot(TOKEN)

# ---------- БАЗА ДАННЫХ ----------
conn = sqlite3.connect("ratings.db", check_same_thread=False)
cursor = conn.cursor()

# Создаём таблицы
cursor.execute("""CREATE TABLE IF NOT EXISTS players (
    name TEXT PRIMARY KEY,
    rating INTEGER DEFAULT 100,
    games_played INTEGER DEFAULT 0,
    games_won INTEGER DEFAULT 0,
    telegram_id INTEGER UNIQUE,
    role TEXT DEFAULT 'user',
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""")
conn.commit()

cursor.execute("""CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player1 TEXT,
    player2 TEXT,
    winner TEXT,
    rating_before_p1 INTEGER,
    rating_before_p2 INTEGER,
    rating_after_p1 INTEGER,
    rating_after_p2 INTEGER,
    k_factor INTEGER DEFAULT 32,
    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(player1) REFERENCES players(name),
    FOREIGN KEY(player2) REFERENCES players(name),
    FOREIGN KEY(winner) REFERENCES players(name)
)""")
conn.commit()

# Проверяем наличие колонок
cursor.execute("PRAGMA table_info(players)")
columns = [col[1] for col in cursor.fetchall()]
if 'role' not in columns:
    cursor.execute("ALTER TABLE players ADD COLUMN role TEXT DEFAULT 'user'")
if 'telegram_id' not in columns:
    cursor.execute("ALTER TABLE players ADD COLUMN telegram_id INTEGER UNIQUE")
if 'registered_at' not in columns:
    cursor.execute("ALTER TABLE players ADD COLUMN registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
conn.commit()

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def get_player_by_id(telegram_id):
    cursor.execute("SELECT name, role FROM players WHERE telegram_id=?", (telegram_id,))
    result = cursor.fetchone()
    return result if result else None

def get_rating(name):
    cursor.execute("SELECT rating FROM players WHERE name=?", (name,))
    result = cursor.fetchone()
    if result:
        return result[0]
    else:
        cursor.execute("INSERT INTO players (name, rating) VALUES (?, 100)", (name,))
        conn.commit()
        return 100

def has_role(telegram_id, required_role):
    cursor.execute("SELECT role FROM players WHERE telegram_id=?", (telegram_id,))
    result = cursor.fetchone()
    if not result:
        return False
    role = result[0]
    roles = {'admin': 3, 'moderator': 2, 'user': 1, 'banned': 0}
    return roles.get(role, 0) >= roles.get(required_role, 0)

def update_elo(winner, loser, k=32):
    rw = get_rating(winner)
    rl = get_rating(loser)
    ew = 1 / (1 + math.pow(10, (rl - rw) / 400))
    el = 1 / (1 + math.pow(10, (rw - rl) / 400))
    new_w = round(rw + k * (1 - ew))
    new_l = round(rl + k * (0 - el))
    
    cursor.execute("UPDATE players SET rating=?, games_played=games_played+1, games_won=games_won+1 WHERE name=?", (new_w, winner))
    cursor.execute("UPDATE players SET rating=?, games_played=games_played+1 WHERE name=?", (new_l, loser))
    cursor.execute("""INSERT INTO matches 
        (player1, player2, winner, rating_before_p1, rating_before_p2, rating_after_p1, rating_after_p2, k_factor)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (winner, loser, winner, rw, rl, new_w, new_l, k))
    conn.commit()
    return new_w, new_l

def register_player(telegram_id, name, role='user'):
    try:
        cursor.execute("INSERT INTO players (name, telegram_id, role) VALUES (?, ?, ?)", (name, telegram_id, role))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

# ---------- КЛАВИАТУРЫ (КНОПКИ) ----------
def main_keyboard():
    """Главное меню с кнопками"""
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn1 = types.KeyboardButton("🏆 Рейтинг")
    btn2 = types.KeyboardButton("👤 Мой профиль")
    btn3 = types.KeyboardButton("📝 Записать матч")
    btn4 = types.KeyboardButton("📊 Статистика")
    btn5 = types.KeyboardButton("❓ Помощь")
    btn6 = types.KeyboardButton("🔧 Админ-панель")
    keyboard.add(btn1, btn2, btn3, btn4, btn5, btn6)
    return keyboard

def admin_keyboard():
    """Кнопки для администратора"""
    keyboard = types.ReplyKeyboardMarkup(row_width=3, resize_keyboard=True)
    btn1 = types.KeyboardButton("👑 Назначить админа")
    btn2 = types.KeyboardButton("🔨 Назначить модератора")
    btn3 = types.KeyboardButton("⛔ Забанить игрока")
    btn4 = types.KeyboardButton("✅ Разбанить игрока")
    btn5 = types.KeyboardButton("📜 История игрока")
    btn6 = types.KeyboardButton("📊 Полная статистика")
    btn7 = types.KeyboardButton("🔄 Сброс рейтинга")
    btn8 = types.KeyboardButton("🗑️ Сброс игр/статистики")
    btn9 = types.KeyboardButton("📥 Загрузить Excel")
    btn10 = types.KeyboardButton("🔙 Назад")
    keyboard.add(btn1, btn2, btn3, btn4, btn5, btn6, btn7, btn8, btn9, btn10)
    return keyboard

def match_keyboard():
    """Кнопки для записи матча"""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    cursor.execute("SELECT name FROM players ORDER BY rating DESC LIMIT 10")
    players = cursor.fetchall()
    
    buttons = []
    for player in players:
        buttons.append(types.InlineKeyboardButton(player[0], callback_data=f"player_{player[0]}"))
    
    # Добавляем кнопку "Отмена"
    buttons.append(types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_match"))
    
    # Разбиваем на ряды по 2
    for i in range(0, len(buttons), 2):
        row = buttons[i:i+2]
        keyboard.row(*row)
    
    return keyboard

def player_selection_keyboard(players, action):
    """Клавиатура для выбора игрока"""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for player in players[:10]:  # Показываем топ-10
        buttons.append(types.InlineKeyboardButton(player[0], callback_data=f"{action}_{player[0]}"))
    buttons.append(types.InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
    
    for i in range(0, len(buttons), 2):
        row = buttons[i:i+2]
        keyboard.row(*row)
    
    return keyboard

# ---------- ОБРАБОТЧИКИ КОМАНД ----------
@bot.message_handler(commands=['start', 'help'])
def send_help(message):
    telegram_id = message.from_user.id
    player = get_player_by_id(telegram_id)
    
    help_text = "🏓 **БОТ ДЛЯ УЧЁТА РЕЙТИНГА В НАСТОЛЬНЫЙ ТЕННИС**\n\n"
    help_text += "Используйте кнопки ниже для навигации!\n\n"
    
    if player:
        name, role = player
        help_text += f"✅ Вы авторизованы как: **{name}** (роль: {role})"
    else:
        help_text += "⚠️ Вы не зарегистрированы! Используйте кнопку '👤 Мой профиль'"
    
    bot.reply_to(message, help_text, parse_mode='Markdown', reply_markup=main_keyboard())

@bot.message_handler(commands=['m'])
def manual_match(message):
    """Быстрый ввод матчей: /m ID1 ID2 ID_победителя | ID3 ID4 ID_победителя"""
    telegram_id = message.from_user.id
    
    # Проверка прав
    if not has_role(telegram_id, 'moderator'):
        bot.reply_to(
            message, 
            "⛔ Только модераторы и администраторы могут записывать матчи!",
            reply_markup=main_keyboard()
        )
        return
    
    # Получаем текст команды
    text = message.text.replace('/m', '').strip()
    
    # Проверяем, есть ли разделители для массового ввода
    if '|' in text:
        # ---------- МАССОВЫЙ ВВОД ----------
        matches = text.split('|')
        results = []
        errors = []
        
        for match_str in matches:
            match_str = match_str.strip()
            if not match_str:
                continue
                
            parts = match_str.split()
            if len(parts) < 3:
                errors.append(f"❌ Неверный формат: '{match_str}' (нужно 3 числа)")
                continue
            
            # Пробуем интерпретировать как ID или имена
            result = process_single_match(parts, message)
            if result:
                results.append(result)
            else:
                errors.append(f"❌ Ошибка в матче: '{match_str}'")
        
        # Формируем ответ
        response = "📊 **РЕЗУЛЬТАТЫ МАССОВОГО ВВОДА**\n\n"
        
        if results:
            response += "✅ **Успешно записано:**\n"
            response += "\n".join(results)
            response += "\n\n"
        
        if errors:
            response += "❌ **Ошибки:**\n"
            response += "\n".join(errors)
        
        bot.reply_to(message, response, parse_mode='Markdown', reply_markup=main_keyboard())
        
    else:
        # ---------- ОДИНОЧНЫЙ МАТЧ ----------
        parts = text.split()
        if len(parts) < 3:
            bot.reply_to(
                message,
                "❌ **Неверный формат!**\n\n"
                "По ID: `/m 1 2 1` (ID игрока1, ID игрока2, ID победителя)\n"
                "По имени: `/m Андрей Петя Андрей`\n"
                "Массово: `/m 1 2 1 | 3 4 3 | 5 6 5`\n\n"
                "💡 Чтобы узнать ID игрока, используйте `/players`",
                parse_mode='Markdown',
                reply_markup=main_keyboard()
            )
            return
        
        result = process_single_match(parts, message)
        if result:
            bot.reply_to(message, result, parse_mode='Markdown', reply_markup=main_keyboard())
        else:
            bot.reply_to(message, "❌ Ошибка при записи матча!", reply_markup=main_keyboard())


def process_single_match(parts, message):
    """Обработка одного матча (по ID или по имени)"""
    # Проверяем, что это ID (все части - числа)
    is_ids = all(part.isdigit() for part in parts[:3])
    
    if is_ids:
        # ---------- ЗАПИСЬ ПО ID ----------
        try:
            id1 = int(parts[0])
            id2 = int(parts[1])
            winner_id = int(parts[2])
        except ValueError:
            return None
        
        # Получаем список всех игроков по рейтингу
        cursor.execute("SELECT name FROM players ORDER BY rating DESC")
        all_players = cursor.fetchall()
        
        if not all_players:
            bot.reply_to(message, "❌ Нет зарегистрированных игроков!", reply_markup=main_keyboard())
            return None
        
        # Проверяем, что ID существуют
        if id1 > len(all_players) or id2 > len(all_players) or winner_id > len(all_players):
            return f"❌ Неверный ID! Всего игроков: {len(all_players)}"
        
        if id1 < 1 or id2 < 1 or winner_id < 1:
            return "❌ ID должен быть больше 0!"
        
        p1 = all_players[id1 - 1][0]
        p2 = all_players[id2 - 1][0]
        winner = all_players[winner_id - 1][0]
        
        if winner not in [p1, p2]:
            return f"❌ Победитель (ID {winner_id}) должен быть одним из игроков!"
        
        if p1 == p2:
            return "❌ Игроки должны быть разными!"
        
        # Проверяем, что победитель не забанен
        cursor.execute("SELECT role FROM players WHERE name=?", (winner,))
        role = cursor.fetchone()
        if role and role[0] == 'banned':
            return f"❌ Игрок {winner} забанен!"
        
        # Записываем матч
        if winner == p1:
            new1, new2 = update_elo(p1, p2)
            response = f"✅ **{p1}** (ID: {id1}) vs **{p2}** (ID: {id2})\n"
            response += f"🏆 {p1}: {new1} (+{new1 - get_rating_before(p1)})\n"
            response += f"📉 {p2}: {new2} ({new2 - get_rating_before(p2)})"
        else:
            new1, new2 = update_elo(p2, p1)
            response = f"✅ **{p1}** (ID: {id1}) vs **{p2}** (ID: {id2})\n"
            response += f"🏆 {p2}: {new1} (+{new1 - get_rating_before(p2)})\n"
            response += f"📉 {p1}: {new2} ({new2 - get_rating_before(p1)})"
        
        return response
        
    else:
        # ---------- ЗАПИСЬ ПО ИМЕНИ ----------
        if len(parts) < 3:
            return None
        
        p1 = parts[0]
        p2 = parts[1]
        winner = parts[2]
        
        # Проверяем, что победитель один из игроков
        if winner not in [p1, p2]:
            return "❌ Победитель должен быть одним из игроков!"
        
        if p1 == p2:
            return "❌ Игроки должны быть разными!"
        
        # Проверяем, что оба игрока существуют
        cursor.execute("SELECT name FROM players WHERE name=? OR name=?", (p1, p2))
        found = cursor.fetchall()
        if len(found) < 2:
            return f"❌ Один из игроков не найден! Зарегистрируйте его через '👤 Мой профиль'"
        
        # Проверяем, что победитель не забанен
        cursor.execute("SELECT role FROM players WHERE name=?", (winner,))
        role = cursor.fetchone()
        if role and role[0] == 'banned':
            return f"❌ Игрок {winner} забанен!"
        
        # Записываем матч
        if winner == p1:
            new1, new2 = update_elo(p1, p2)
            response = f"✅ **{p1}** vs **{p2}**\n"
            response += f"🏆 {p1}: {new1} (+{new1 - get_rating_before(p1)})\n"
            response += f"📉 {p2}: {new2} ({new2 - get_rating_before(p2)})"
        else:
            new1, new2 = update_elo(p2, p1)
            response = f"✅ **{p1}** vs **{p2}**\n"
            response += f"🏆 {p2}: {new1} (+{new1 - get_rating_before(p2)})\n"
            response += f"📉 {p1}: {new2} ({new2 - get_rating_before(p1)})"
        
        return response

@bot.message_handler(func=lambda message: message.text == "🏆 Рейтинг")
def show_rating(message):
    cursor.execute("SELECT name, rating, games_played, games_won FROM players ORDER BY rating DESC")
    data = cursor.fetchall()
    if not data:
        bot.reply_to(message, "Игроков пока нет. Зарегистрируйтесь первым!")
        return
    
    text = "🏆 **ТАБЛИЦА РЕЙТИНГА**\n\n"
    for i, row in enumerate(data[:20], 1):
        name, rating, games, wins = row
        winrate = round(wins/games*100, 1) if games > 0 else 0
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} {name} — **{rating}** pts (Игр: {games}, {winrate}%)\n"
    
    bot.reply_to(message, text, parse_mode='Markdown', reply_markup=main_keyboard())

@bot.message_handler(func=lambda message: message.text == "👤 Мой профиль")
def my_profile(message):
    telegram_id = message.from_user.id
    player = get_player_by_id(telegram_id)
    
    if not player:
        # Предлагаем зарегистрироваться
        msg = bot.reply_to(message, "❌ Вы не зарегистрированы!\nВведите ваше имя для регистрации:")
        bot.register_next_step_handler(msg, register_name)
        return
    
    name, role = player
    cursor.execute("SELECT rating, games_played, games_won FROM players WHERE name=?", (name,))
    rating, games, wins = cursor.fetchone()
    winrate = round(wins/games*100, 1) if games > 0 else 0
    
    text = f"📊 **Профиль игрока:** {name}\n"
    text += f"🎯 Рейтинг: **{rating}**\n"
    text += f"📈 Игр сыграно: {games}\n"
    text += f"🏆 Побед: {wins} ({winrate}%)\n"
    text += f"👑 Роль: {role}\n"
    text += f"🆔 Telegram ID: {message.from_user.id}"
    
    bot.reply_to(message, text, parse_mode='Markdown', reply_markup=main_keyboard())

def register_name(message):
    name = message.text.strip()
    telegram_id = message.from_user.id
    
    if get_player_by_id(telegram_id):
        bot.reply_to(message, "❌ Вы уже зарегистрированы!", reply_markup=main_keyboard())
        return
    
    cursor.execute("SELECT name FROM players WHERE name=?", (name,))
    if cursor.fetchone():
        bot.reply_to(message, f"❌ Имя '{name}' уже занято!", reply_markup=main_keyboard())
        return
    
    if register_player(telegram_id, name, 'user'):
        bot.reply_to(message, f"✅ Поздравляю, {name}! Вы зарегистрированы!\nВаш начальный рейтинг: 100", reply_markup=main_keyboard())
    else:
        bot.reply_to(message, "❌ Ошибка регистрации", reply_markup=main_keyboard())

@bot.message_handler(func=lambda message: message.text == "📝 Записать матч")
def start_match(message):
    telegram_id = message.from_user.id
    player = get_player_by_id(telegram_id)
    
    if not player:
        bot.reply_to(message, "❌ Сначала зарегистрируйтесь через '👤 Мой профиль'!", reply_markup=main_keyboard())
        return
    
    if player[1] == 'banned':
        bot.reply_to(message, "⛔ Вы забанены и не можете участвовать!", reply_markup=main_keyboard())
        return
    
    # ⚠️ ПРОВЕРКА: только модератор или админ может записывать матчи
    if not has_role(telegram_id, 'moderator'):
        bot.reply_to(
            message, 
            "⛔ Только модераторы и администраторы могут записывать матчи!\n"
            "Обратитесь к администратору для получения прав.",
            reply_markup=main_keyboard()
        )
        return
    
    # Показываем меню выбора способа записи матча
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    btn1 = types.InlineKeyboardButton("👥 Выбрать из списка", callback_data="match_select_from_list")
    btn2 = types.InlineKeyboardButton("✏️ Ввести вручную", callback_data="match_manual_input")
    btn3 = types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_match")
    keyboard.add(btn1, btn2, btn3)
    
    bot.reply_to(
        message,
        "📝 **Как хотите записать матч?**\n\n"
        "• **Выбрать из списка** — выбрать игроков из топ-10\n"
        "• **Ввести вручную** — ввести имена игроков и победителя\n"
        "(например: /m Андрей Петя Андрей или /m ID Игрока1, ID Игрока2) \n"
        "также есть способ массового ввода с помощью | \n"
        "(например: /m 1 2 1 | 2 3 3 | Андрей Юра Андрей)",
        parse_mode='Markdown' \
        '',
        reply_markup=keyboard
    )

@bot.message_handler(func=lambda message: message.text == "📊 Статистика")
def show_statistics(message):
    """Показать общую статистику для всех пользователей"""
    
    # Основная статистика
    cursor.execute("SELECT COUNT(*) FROM players")
    total_players = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM matches")
    total_matches = cursor.fetchone()[0]
    
    cursor.execute("SELECT AVG(rating) FROM players")
    avg_rating = round(cursor.fetchone()[0] or 0)
    
    cursor.execute("SELECT MAX(rating) FROM players")
    max_rating = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT MIN(rating) FROM players")
    min_rating = cursor.fetchone()[0] or 0
    
    # Топ-3 игроков
    cursor.execute("SELECT name, rating FROM players ORDER BY rating DESC LIMIT 3")
    top_players = cursor.fetchall()
    
    # Активные игроки (больше всего матчей)
    cursor.execute("SELECT name, games_played FROM players ORDER BY games_played DESC LIMIT 3")
    active_players = cursor.fetchall()
    
    # Самый результативный (больше всего побед)
    cursor.execute("SELECT name, games_won FROM players ORDER BY games_won DESC LIMIT 3")
    best_winners = cursor.fetchall()
    
    # Статистика по ролям
    cursor.execute("SELECT role, COUNT(*) FROM players GROUP BY role")
    roles_stats = cursor.fetchall()
    
    # Формируем текст
    text = "📊 **ОБЩАЯ СТАТИСТИКА СИСТЕМЫ**\n\n"
    
    text += "📌 **Общая информация:**\n"
    text += f"👥 Всего игроков: **{total_players}**\n"
    text += f"🏓 Всего матчей: **{total_matches}**\n"
    text += f"📈 Средний рейтинг: **{avg_rating}**\n"
    text += f"⬆️ Максимальный рейтинг: **{max_rating}**\n"
    text += f"⬇️ Минимальный рейтинг: **{min_rating}**\n\n"
    
    # Топ-3 игроков
    if top_players:
        text += "🏆 **Топ-3 игроков:**\n"
        medals = ["🥇", "🥈", "🥉"]
        for i, (name, rating) in enumerate(top_players):
            text += f"{medals[i]} {name} — **{rating}** pts\n"
        text += "\n"
    
    # Активные игроки
    if active_players and active_players[0][1] > 0:
        text += "🔥 **Самые активные игроки:**\n"
        for i, (name, games) in enumerate(active_players[:3], 1):
            text += f"{i}. {name} — {games} игр\n"
        text += "\n"
    
    # Самые результативные
    if best_winners and best_winners[0][1] > 0:
        text += "💪 **Самые результативные:**\n"
        for i, (name, wins) in enumerate(best_winners[:3], 1):
            # Считаем процент побед
            cursor.execute("SELECT games_played FROM players WHERE name=?", (name,))
            games = cursor.fetchone()[0]
            winrate = round(wins / games * 100, 1) if games > 0 else 0
            text += f"{i}. {name} — {wins} побед ({winrate}%)\n"
        text += "\n"
    
    # Статистика по ролям
    if roles_stats:
        text += "👑 **Распределение ролей:**\n"
        role_emojis = {'admin': '👑', 'moderator': '🔨', 'user': '👤', 'banned': '⛔'}
        for role, count in roles_stats:
            emoji = role_emojis.get(role, '❓')
            text += f"{emoji} {role}: {count}\n"
    
    # Инлайн-кнопки для дополнительной статистики
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    btn1 = types.InlineKeyboardButton("📋 Полная таблица", callback_data="full_rating_table")
    btn2 = types.InlineKeyboardButton("📈 Моя статистика", callback_data="my_stats")
    btn3 = types.InlineKeyboardButton("🔄 Обновить", callback_data="refresh_stats")
    keyboard.add(btn1, btn2, btn3)
    
    bot.reply_to(message, text, parse_mode='Markdown', reply_markup=keyboard)

@bot.message_handler(func=lambda message: message.text == "❓ Помощь")
def help_command(message):
    send_help(message)

@bot.message_handler(func=lambda message: message.text == "🔧 Админ-панель")
def admin_panel(message):
    telegram_id = message.from_user.id
    if not has_role(telegram_id, 'moderator'):
        bot.reply_to(message, "⛔ У вас нет доступа к админ-панели!", reply_markup=main_keyboard())
        return
    
    bot.reply_to(message, "🔧 **Админ-панель**\nВыберите действие:", parse_mode='Markdown', reply_markup=admin_keyboard())

@bot.message_handler(func=lambda message: message.text == "🔙 Назад")
def back_to_main(message):
    bot.reply_to(message, "🔙 Возврат в главное меню", reply_markup=main_keyboard())


# ---------- ОБРАБОТЧИКИ АДМИН-ПАНЕЛИ ----------

@bot.message_handler(func=lambda message: message.text == "📥 Загрузить Excel")
def upload_matches_button(message):
    """Кнопка для загрузки матчей"""
    telegram_id = message.from_user.id
    
    if not has_role(telegram_id, 'moderator'):
        bot.reply_to(message, "⛔ У вас нет прав!", reply_markup=main_keyboard())
        return
    
    bot.reply_to(
        message,
        "🏓 **Загрузка матчей из Excel**\n\n"
        "Отправьте мне Excel-файл (.xlsx или .xls) со следующими колонками:\n\n"
        "• **Игрок1** — имя первого игрока\n"
        "• **Игрок2** — имя второго игрока\n"
        "• **Победитель** — кто победил\n\n"
        "📌 **Пример:**\n"
        "| Игрок1 | Игрок2 | Победитель |\n"
        "| Андрей | Петя | Андрей |\n"
        "| Сергей | Искандер | Искандер |\n\n"
        "⚠️ Все игроки должны быть зарегистрированы в системе!",
        parse_mode='Markdown',
        reply_markup=admin_keyboard()
    )

# ---------- СБРОС РЕЙТИНГА ----------
@bot.message_handler(func=lambda message: message.text == "🔄 Сброс рейтинга")
def reset_rating_button(message):
    telegram_id = message.from_user.id
    if not has_role(telegram_id, 'moderator'):
        bot.reply_to(message, "⛔ У вас нет прав!", reply_markup=main_keyboard())
        return
    
    # Показываем меню сброса рейтинга
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    btn1 = types.InlineKeyboardButton("👤 Сбросить рейтинг игроку", callback_data="reset_one_rating")
    btn2 = types.InlineKeyboardButton("👥 Сбросить рейтинг ВСЕМ", callback_data="reset_all_rating")
    btn3 = types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_reset")
    keyboard.add(btn1, btn2, btn3)
    
    bot.reply_to(
        message, 
        "🔄 **Выберите действие:**\n\n"
        "• Сбросить рейтинг конкретному игроку до 100\n"
        "• Сбросить рейтинг ВСЕМ игрокам до 100",
        parse_mode='Markdown',
        reply_markup=keyboard
    )

@bot.message_handler(func=lambda message: message.text == "🗑️ Сброс игр/статистики")
def reset_stats_button(message):
    telegram_id = message.from_user.id
    if not has_role(telegram_id, 'moderator'):
        bot.reply_to(message, "⛔ У вас нет прав!", reply_markup=main_keyboard())
        return
    
    # Показываем меню сброса статистики
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    btn1 = types.InlineKeyboardButton("👤 Сбросить статистику игроку", callback_data="reset_one_stats")
    btn2 = types.InlineKeyboardButton("👥 Сбросить статистику ВСЕМ", callback_data="reset_all_stats")
    btn3 = types.InlineKeyboardButton("🗑️ Очистить историю матчей", callback_data="clear_matches")
    btn4 = types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_reset")
    keyboard.add(btn1, btn2, btn3, btn4)
    
    bot.reply_to(
        message, 
        "🗑️ **Выберите действие:**\n\n"
        "• Сбросить статистику (игры, победы) игроку\n"
        "• Сбросить статистику ВСЕМ игрокам\n"
        "• Очистить историю матчей",
        parse_mode='Markdown',
        reply_markup=keyboard
    )

@bot.message_handler(commands=['reset_all_ratings'])
def reset_all_ratings(message):
    # Проверка, что это админ
    if not has_role(message.from_user.id, 'admin'):
        bot.reply_to(message, "⛔ Только администратор может сбросить все рейтинги!")
        return
    
    # Сброс всех рейтингов до 100
    cursor.execute("UPDATE players SET rating=100")
    conn.commit()
    
    bot.reply_to(message, "✅ Рейтинг ВСЕХ игроков сброшен до 100!")

@bot.message_handler(func=lambda message: message.text == "👑 Назначить админа")
def give_admin_button(message):
    telegram_id = message.from_user.id
    if not has_role(telegram_id, 'admin'):
        bot.reply_to(message, "⛔ Только администратор может назначать админов!", reply_markup=main_keyboard())
        return
    
    msg = bot.reply_to(message, "👑 Введите имя игрока, которому хотите назначить роль ADMIN:")
    bot.register_next_step_handler(msg, process_give_admin)

def process_give_admin(message):
    name = message.text.strip()
    cursor.execute("UPDATE players SET role='admin' WHERE name=?", (name,))
    conn.commit()
    
    if cursor.rowcount > 0:
        bot.reply_to(message, f"✅ Игроку {name} назначена роль ADMIN!", reply_markup=admin_keyboard())
    else:
        bot.reply_to(message, f"❌ Игрок {name} не найден!", reply_markup=admin_keyboard())

@bot.message_handler(func=lambda message: message.text == "🔨 Назначить модератора")
def give_moderator_button(message):
    telegram_id = message.from_user.id
    if not has_role(telegram_id, 'admin'):
        bot.reply_to(message, "⛔ Только администратор может назначать модераторов!", reply_markup=main_keyboard())
        return
    
    msg = bot.reply_to(message, "🔨 Введите имя игрока, которому хотите назначить роль MODERATOR:")
    bot.register_next_step_handler(msg, process_give_moderator)

def process_give_moderator(message):
    name = message.text.strip()
    cursor.execute("UPDATE players SET role='moderator' WHERE name=?", (name,))
    conn.commit()
    
    if cursor.rowcount > 0:
        bot.reply_to(message, f"✅ Игроку {name} назначена роль MODERATOR!", reply_markup=admin_keyboard())
    else:
        bot.reply_to(message, f"❌ Игрок {name} не найден!", reply_markup=admin_keyboard())

@bot.message_handler(func=lambda message: message.text == "⛔ Забанить игрока")
def ban_player_button(message):
    telegram_id = message.from_user.id
    if not has_role(telegram_id, 'admin'):
        bot.reply_to(message, "⛔ Только администратор может банить!", reply_markup=main_keyboard())
        return
    
    msg = bot.reply_to(message, "⛔ Введите имя игрока для бана:")
    bot.register_next_step_handler(msg, process_ban_player)

def process_ban_player(message):
    name = message.text.strip()
    cursor.execute("UPDATE players SET role='banned' WHERE name=?", (name,))
    conn.commit()
    
    if cursor.rowcount > 0:
        bot.reply_to(message, f"⛔ Игрок {name} забанен!", reply_markup=admin_keyboard())
    else:
        bot.reply_to(message, f"❌ Игрок {name} не найден!", reply_markup=admin_keyboard())

@bot.message_handler(func=lambda message: message.text == "✅ Разбанить игрока")
def unban_player_button(message):
    telegram_id = message.from_user.id
    if not has_role(telegram_id, 'admin'):
        bot.reply_to(message, "⛔ Только администратор может разбанивать!", reply_markup=main_keyboard())
        return
    
    msg = bot.reply_to(message, "✅ Введите имя игрока для разбана:")
    bot.register_next_step_handler(msg, process_unban_player)

def process_unban_player(message):
    name = message.text.strip()
    cursor.execute("UPDATE players SET role='user' WHERE name=?", (name,))
    conn.commit()
    
    if cursor.rowcount > 0:
        bot.reply_to(message, f"✅ Игрок {name} разбанен!", reply_markup=admin_keyboard())
    else:
        bot.reply_to(message, f"❌ Игрок {name} не найден!", reply_markup=admin_keyboard())

@bot.message_handler(func=lambda message: message.text == "📜 История игрока")
def history_button(message):
    telegram_id = message.from_user.id
    if not has_role(telegram_id, 'moderator'):
        bot.reply_to(message, "⛔ У вас нет прав!", reply_markup=main_keyboard())
        return
    
    msg = bot.reply_to(message, "📜 Введите имя игрока для просмотра истории:")
    bot.register_next_step_handler(msg, process_history)

def process_history(message):
    name = message.text.strip()
    cursor.execute("""SELECT player1, player2, winner, rating_before_p1, rating_before_p2, 
                      rating_after_p1, rating_after_p2, played_at 
                      FROM matches 
                      WHERE player1=? OR player2=?
                      ORDER BY played_at DESC LIMIT 10""", (name, name))
    matches = cursor.fetchall()
    
    if not matches:
        bot.reply_to(message, f"📜 У игрока {name} нет сыгранных матчей", reply_markup=admin_keyboard())
        return
    
    text = f"📜 **История матчей {name}** (последние 10):\n\n"
    for m in matches:
        p1, p2, winner, rb1, rb2, ra1, ra2, time = m
        if name == p1:
            change = ra1 - rb1
            opponent = p2
        else:
            change = ra2 - rb2
            opponent = p1
        sign = "+" if change > 0 else ""
        result = "✅ Победа" if winner == name else "❌ Поражение"
        text += f"vs {opponent} → {result} (изменение: {sign}{change})\n"
    
    bot.reply_to(message, text, parse_mode='Markdown', reply_markup=admin_keyboard())

@bot.message_handler(func=lambda message: message.text == "📊 Полная статистика")
def full_stats_button(message):
    telegram_id = message.from_user.id
    if not has_role(telegram_id, 'admin'):
        bot.reply_to(message, "⛔ Только администратор может видеть полную статистику!", reply_markup=main_keyboard())
        return
    
    cursor.execute("SELECT COUNT(*) FROM players")
    total_players = cursor.fetchone()[0]
    
    cursor.execute("SELECT AVG(rating) FROM players")
    avg_rating = round(cursor.fetchone()[0] or 0)
    
    cursor.execute("SELECT COUNT(*) FROM matches")
    total_matches = cursor.fetchone()[0]
    
    cursor.execute("SELECT name, rating FROM players ORDER BY rating DESC LIMIT 1")
    best = cursor.fetchone()
    
    text = "📊 **ПОЛНАЯ СТАТИСТИКА**\n\n"
    text += f"👥 Всего игроков: {total_players}\n"
    text += f"🎯 Средний рейтинг: {avg_rating}\n"
    text += f"🏓 Всего матчей: {total_matches}\n"
    if best:
        text += f"🥇 Лидер: {best[0]} ({best[1]} pts)\n"
    
    cursor.execute("SELECT role, COUNT(*) FROM players GROUP BY role")
    roles = cursor.fetchall()
    text += "\n**Роли:**\n"
    for role, count in roles:
        text += f"  {role}: {count}\n"
    
    bot.reply_to(message, text, parse_mode='Markdown', reply_markup=admin_keyboard())


@bot.message_handler(commands=['load'])
def load_excel_command(message):
    """Команда для загрузки Excel: /load"""
    if not has_role(message.from_user.id, 'moderator'):
        bot.reply_to(message, "⛔ Только модераторы!", reply_markup=main_keyboard())
        return
    
    bot.reply_to(
        message,
        "📥 **Загрузите Excel-файл**\n\n"
        "Отправьте файл с колонками:\n"
        "• Игрок1\n"
        "• Игрок2\n"
        "• Победитель",
        reply_markup=admin_keyboard()
    )
# ---------- ОБРАБОТЧИКИ ИНЛАЙН-КНОПОК ----------
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    data = call.data
    
    # ---------- ВЫБОР СОПЕРНИКА ----------
    if data.startswith("opponent_"):
        opponent = data.replace("opponent_", "")
        player = get_player_by_id(call.from_user.id)
        
        if not player:
            bot.answer_callback_query(call.id, "❌ Вы не зарегистрированы!")
            return
        
        text = f"✅ Соперник: {opponent}\nКто победил?"
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        btn1 = types.InlineKeyboardButton(f"🏆 {player[0]}", callback_data=f"win_{player[0]}_{opponent}")
        btn2 = types.InlineKeyboardButton(f"🏆 {opponent}", callback_data=f"win_{opponent}_{player[0]}")
        btn3 = types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_match")
        keyboard.add(btn1, btn2, btn3)
        
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=keyboard)
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, text, reply_markup=keyboard)
        bot.answer_callback_query(call.id)
    
    # ---------- ПОБЕДА ----------
    elif data.startswith("win_"):
        parts = data.split("_")
        winner = parts[1]
        loser = parts[2]
        
        cursor.execute("SELECT name FROM players WHERE name=?", (winner,))
        if not cursor.fetchone():
            bot.answer_callback_query(call.id, "❌ Игрок не найден!")
            return
        
        new_w, new_l = update_elo(winner, loser)
        
        text = f"✅ **Матч записан!**\n\n"
        text += f"🏆 {winner}: {new_w} (+{new_w - get_rating_before(winner)})\n"
        text += f"📉 {loser}: {new_l} ({new_l - get_rating_before(loser)})"
        
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, text, parse_mode='Markdown')
        
        bot.answer_callback_query(call.id, "✅ Матч записан!")
        bot.send_message(call.message.chat.id, f"🎉 Поздравляем {winner} с победой!", reply_markup=main_keyboard())
    
    # ---------- ОТМЕНА МАТЧА ----------
    elif data == "cancel_match":
        try:
            bot.edit_message_text("❌ Запись матча отменена", call.message.chat.id, call.message.message_id)
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, "❌ Запись матча отменена")
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "🔙 Возврат в главное меню", reply_markup=main_keyboard())
    
    # ---------- ОТМЕНА ----------
    elif data == "cancel":
        try:
            bot.edit_message_text("❌ Отменено", call.message.chat.id, call.message.message_id)
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, "❌ Отменено")
        bot.answer_callback_query(call.id)
    
    # ---------- СБРОС РЕЙТИНГА ----------
    elif data == "reset_one_rating":
        try:
            bot.edit_message_text("👤 Введите имя игрока для сброса рейтинга до 100:", call.message.chat.id, call.message.message_id)
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, "👤 Введите имя игрока для сброса рейтинга до 100:")
        msg = bot.send_message(call.message.chat.id, "Введите имя:")
        bot.register_next_step_handler(msg, process_reset_one_rating)
        bot.answer_callback_query(call.id)
    
    elif data == "reset_all_rating":
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        btn1 = types.InlineKeyboardButton("✅ ДА, сбросить ВСЕМ", callback_data="confirm_reset_all_rating")
        btn2 = types.InlineKeyboardButton("❌ НЕТ, отмена", callback_data="cancel_reset")
        keyboard.add(btn1, btn2)
        
        try:
            bot.edit_message_text(
                "⚠️ **ВНИМАНИЕ!**\n\nВы уверены, что хотите сбросить рейтинг ВСЕМ игрокам до 100?\nЭто действие НЕЛЬЗЯ отменить!",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=keyboard
            )
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(
                call.message.chat.id,
                "⚠️ **ВНИМАНИЕ!**\n\nВы уверены, что хотите сбросить рейтинг ВСЕМ игрокам до 100?\nЭто действие НЕЛЬЗЯ отменить!",
                parse_mode='Markdown',
                reply_markup=keyboard
            )
        bot.answer_callback_query(call.id)
    
    elif data == "confirm_reset_all_rating":
        cursor.execute("UPDATE players SET rating=100")
        conn.commit()
        
        try:
            bot.edit_message_text("✅ **Рейтинг ВСЕХ игроков сброшен до 100!**", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, "✅ **Рейтинг ВСЕХ игроков сброшен до 100!**", parse_mode='Markdown')
        
        bot.send_message(call.message.chat.id, "🔙 Возврат в главное меню", reply_markup=main_keyboard())
        bot.answer_callback_query(call.id, "✅ Рейтинг всех сброшен!")
    
    # ---------- СБРОС СТАТИСТИКИ ----------
    elif data == "reset_one_stats":
        try:
            bot.edit_message_text("👤 Введите имя игрока для сброса статистики:", call.message.chat.id, call.message.message_id)
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, "👤 Введите имя игрока для сброса статистики:")
        msg = bot.send_message(call.message.chat.id, "Введите имя:")
        bot.register_next_step_handler(msg, process_reset_one_stats)
        bot.answer_callback_query(call.id)
    
    elif data == "reset_all_stats":
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        btn1 = types.InlineKeyboardButton("✅ ДА, сбросить ВСЕМ", callback_data="confirm_reset_all_stats")
        btn2 = types.InlineKeyboardButton("❌ НЕТ, отмена", callback_data="cancel_reset")
        keyboard.add(btn1, btn2)
        
        try:
            bot.edit_message_text(
                "⚠️ **ВНИМАНИЕ!**\n\nВы уверены, что хотите сбросить статистику ВСЕМ игрокам?\nЭто действие НЕЛЬЗЯ отменить!",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=keyboard
            )
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(
                call.message.chat.id,
                "⚠️ **ВНИМАНИЕ!**\n\nВы уверены, что хотите сбросить статистику ВСЕМ игрокам?\nЭто действие НЕЛЬЗЯ отменить!",
                parse_mode='Markdown',
                reply_markup=keyboard
            )
        bot.answer_callback_query(call.id)
    
    elif data == "confirm_reset_all_stats":
        cursor.execute("UPDATE players SET games_played=0, games_won=0")
        conn.commit()
        
        try:
            bot.edit_message_text("✅ **Статистика ВСЕХ игроков сброшена!**", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, "✅ **Статистика ВСЕХ игроков сброшена!**", parse_mode='Markdown')
        
        bot.send_message(call.message.chat.id, "🔙 Возврат в главное меню", reply_markup=main_keyboard())
        bot.answer_callback_query(call.id, "✅ Статистика всех сброшена!")
    
    # ---------- ОЧИСТКА ИСТОРИИ ----------
    elif data == "clear_matches":
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        btn1 = types.InlineKeyboardButton("✅ ДА, очистить", callback_data="confirm_clear_matches")
        btn2 = types.InlineKeyboardButton("❌ НЕТ, отмена", callback_data="cancel_reset")
        keyboard.add(btn1, btn2)
        
        try:
            bot.edit_message_text(
                "⚠️ **ВНИМАНИЕ!**\n\nВы уверены, что хотите очистить ВСЮ историю матчей?\nЭто действие НЕЛЬЗЯ отменить!",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=keyboard
            )
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(
                call.message.chat.id,
                "⚠️ **ВНИМАНИЕ!**\n\nВы уверены, что хотите очистить ВСЮ историю матчей?\nЭто действие НЕЛЬЗЯ отменить!",
                parse_mode='Markdown',
                reply_markup=keyboard
            )
        bot.answer_callback_query(call.id)
    
    elif data == "confirm_clear_matches":
        cursor.execute("DELETE FROM matches")
        conn.commit()
        
        try:
            bot.edit_message_text("✅ **Вся история матчей очищена!**", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, "✅ **Вся история матчей очищена!**", parse_mode='Markdown')
        
        bot.send_message(call.message.chat.id, "🔙 Возврат в главное меню", reply_markup=main_keyboard())
        bot.answer_callback_query(call.id, "✅ История очищена!")
    
    # ---------- ОТМЕНА ДЕЙСТВИЙ ----------
    elif data == "cancel_reset":
        try:
            bot.edit_message_text("❌ Действие отменено", call.message.chat.id, call.message.message_id)
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, "❌ Действие отменено")
        bot.send_message(call.message.chat.id, "🔙 Возврат в главное меню", reply_markup=main_keyboard())
        bot.answer_callback_query(call.id)
    
    # ---------- ПОЛНАЯ ТАБЛИЦА ----------
    elif data == "full_rating_table":
        cursor.execute("SELECT name, rating, games_played, games_won FROM players ORDER BY rating DESC")
        players = cursor.fetchall()
        
        if not players:
            bot.answer_callback_query(call.id, "Нет игроков!")
            return
        
        text = "📋 **ПОЛНАЯ ТАБЛИЦА РЕЙТИНГА**\n\n"
        for i, (name, rating, games, wins) in enumerate(players, 1):
            winrate = round(wins / games * 100, 1) if games > 0 else 0
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            text += f"{medal} {name} — **{rating}** pts (Игр: {games}, Побед: {wins}, {winrate}%)\n"
        
        try:
            if len(text) > 4000:
                with open("rating_table.txt", "w", encoding="utf-8") as f:
                    f.write(text)
                bot.send_document(call.message.chat.id, open("rating_table.txt", "rb"))
                bot.answer_callback_query(call.id, "Таблица отправлена файлом!")
            else:
                bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='Markdown')
                bot.answer_callback_query(call.id)
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, text, parse_mode='Markdown')
            bot.answer_callback_query(call.id)
    
    # ---------- МОЯ СТАТИСТИКА ----------
    elif data == "my_stats":
        player = get_player_by_id(call.from_user.id)
        if not player:
            bot.answer_callback_query(call.id, "❌ Вы не зарегистрированы!")
            return
        
        name, role = player
        cursor.execute("SELECT rating, games_played, games_won FROM players WHERE name=?", (name,))
        rating, games, wins = cursor.fetchone()
        winrate = round(wins / games * 100, 1) if games > 0 else 0
        
        cursor.execute("SELECT COUNT(*) + 1 FROM players WHERE rating > ?", (rating,))
        position = cursor.fetchone()[0]
        
        text = f"📈 **МОЯ СТАТИСТИКА**\n\n"
        text += f"👤 Имя: **{name}**\n"
        text += f"👑 Роль: {role}\n"
        text += f"🏆 Рейтинг: **{rating}**\n"
        text += f"📍 Место в рейтинге: **{position}**\n"
        text += f"📈 Игр сыграно: **{games}**\n"
        text += f"🏅 Побед: **{wins}**\n"
        text += f"📊 Процент побед: **{winrate}%**\n"
        
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, text, parse_mode='Markdown')
        bot.answer_callback_query(call.id)
    
    # ---------- ОБНОВЛЕНИЕ СТАТИСТИКИ ----------
    elif data == "refresh_stats":
        try:
            bot.edit_message_text("🔄 Обновление статистики...", call.message.chat.id, call.message.message_id)
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, "🔄 Обновление статистики...")
        bot.answer_callback_query(call.id, "✅ Статистика обновлена!")
        show_statistics(call.message)
    
    # ---------- ВЫБОР СПОСОБА ЗАПИСИ МАТЧА ----------
    elif data == "match_select_from_list":
        player = get_player_by_id(call.from_user.id)
        if not player:
            bot.answer_callback_query(call.id, "❌ Вы не зарегистрированы!")
            return
        
        cursor.execute("SELECT name FROM players WHERE name != ? ORDER BY rating DESC LIMIT 10", (player[0],))
        players = cursor.fetchall()
        
        if not players:
            bot.answer_callback_query(call.id, "❌ Нет других игроков!")
            return
        
        text = "👥 **Выберите соперника:**\n(показаны топ-10 игроков)"
        keyboard = player_selection_keyboard(players, "opponent")
        
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=keyboard)
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, text, parse_mode='Markdown', reply_markup=keyboard)
        bot.answer_callback_query(call.id)
    
    elif data == "match_manual_input":
        try:
            bot.edit_message_text(
                "✏️ **Введите матч в формате:**\n\n"
                "По ID: `/m 1 2 1`\n"
                "По имени: `/m Андрей Петя Андрей`",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown'
            )
        except:
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(
                call.message.chat.id,
                "✏️ **Введите матч в формате:**\n\n"
                "По ID: `/m 1 2 1`\n"
                "По имени: `/m Андрей Петя Андрей`",
                parse_mode='Markdown'
            )
        bot.answer_callback_query(call.id)

def get_rating_before(name):
    cursor.execute("SELECT rating FROM players WHERE name=?", (name,))
    result = cursor.fetchone()
    return result[0] if result else 100


# ---------- ОБРАБОТКА EXCEL-ФАЙЛОВ ----------
@bot.message_handler(content_types=['document'])
def handle_excel_file(message):
    """Обработка загруженного Excel-файла (простой способ)"""
    telegram_id = message.from_user.id
    
    if not has_role(telegram_id, 'moderator'):
        bot.reply_to(message, "⛔ Только модераторы!", reply_markup=main_keyboard())
        return
    
    file_info = message.document
    file_name = file_info.file_name
    
    if not file_name.endswith(('.xlsx', '.xls')):
        bot.reply_to(message, "❌ Нужен .xlsx или .xls", reply_markup=main_keyboard())
        return
    
    status_msg = bot.reply_to(message, "🔄 Загружаю и обрабатываю файл...")
    
    try:
        # Получаем файл через get_file
        file_info = bot.get_file(file_info.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        temp_file = f"temp_{file_name}"
        with open(temp_file, "wb") as f:
            f.write(downloaded_file)
        
        df = pd.read_excel(temp_file, engine='openpyxl')
        os.remove(temp_file)
        
        # Проверяем, что есть данные
        if df.empty:
            bot.reply_to(message, "❌ В файле нет данных!", reply_markup=admin_keyboard())
            return
        
        # Показываем, что прочитали
        columns = df.columns.tolist()
        rows_count = len(df)
        
        # Ищем нужные колонки
        found_columns = []
        for col in columns:
            col_lower = str(col).strip().lower()
            if 'игрок1' in col_lower or 'player1' in col_lower:
                found_columns.append(('Игрок1', col))
            elif 'игрок2' in col_lower or 'player2' in col_lower:
                found_columns.append(('Игрок2', col))
            elif 'победитель' in col_lower or 'winner' in col_lower:
                found_columns.append(('Победитель', col))
        
        # Если найдены все 3 колонки - обрабатываем матчи
        if len(found_columns) >= 3:
            results = process_excel_matches_simple(df, found_columns)
            response = "🏓 **РЕЗУЛЬТАТЫ ЗАГРУЗКИ МАТЧЕЙ**\n\n" + results
        else:
            # Иначе пробуем как файл с игроками
            results = process_excel_data_simple(df)
            response = "📊 **РЕЗУЛЬТАТЫ ЗАГРУЗКИ ИГРОКОВ**\n\n" + results
        
        bot.reply_to(message, response, parse_mode='Markdown', reply_markup=admin_keyboard())
        
    except Exception as e:
        error_text = str(e)
        bot.delete_message(status_msg.chat.id, status_msg.message_id)
        bot.send_message(
            message.chat.id,
            f"❌ Ошибка: {error_text}",
            reply_markup=admin_keyboard()
        )


def process_excel_matches_simple(df, found_columns):
    """Простая обработка матчей из Excel"""
    success = []
    errors = []
    
    # Находим названия колонок
    p1_col = None
    p2_col = None
    win_col = None
    
    for role, col in found_columns:
        if role == 'Игрок1':
            p1_col = col
        elif role == 'Игрок2':
            p2_col = col
        elif role == 'Победитель':
            win_col = col
    
    # Проходим по строкам
    for idx, row in df.iterrows():
        try:
            p1 = str(row[p1_col]).strip()
            p2 = str(row[p2_col]).strip()
            winner = str(row[win_col]).strip()
            
            # Пропускаем пустые
            if not p1 or not p2 or not winner:
                continue
            if p1 == 'nan' or p2 == 'nan' or winner == 'nan':
                continue
            
            # Проверяем, что победитель один из игроков
            if winner not in [p1, p2]:
                errors.append(f"❌ Строка {idx+2}: победитель '{winner}' не является игроком!")
                continue
            
            if p1 == p2:
                errors.append(f"❌ Строка {idx+2}: игроки должны быть разными!")
                continue
            
            # Проверяем существование игроков
            cursor.execute("SELECT name FROM players WHERE name=?", (p1,))
            if not cursor.fetchone():
                errors.append(f"❌ Строка {idx+2}: игрок '{p1}' не найден!")
                continue
            
            cursor.execute("SELECT name FROM players WHERE name=?", (p2,))
            if not cursor.fetchone():
                errors.append(f"❌ Строка {idx+2}: игрок '{p2}' не найден!")
                continue
            
            # Записываем матч
            if winner == p1:
                new_w, new_l = update_elo(p1, p2)
                success.append(f"✅ {p1} победил {p2} (+{new_w - get_rating_before(p1)})")
            else:
                new_w, new_l = update_elo(p2, p1)
                success.append(f"✅ {p2} победил {p1} (+{new_w - get_rating_before(p2)})")
                
        except Exception as e:
            errors.append(f"❌ Строка {idx+2}: {str(e)}")
    
    # Формируем ответ
    response = ""
    if success:
        response += f"✅ **Записано матчей:** {len(success)}\n"
        response += "\n".join(success[:10])
        if len(success) > 10:
            response += f"\n... и ещё {len(success) - 10}"
        response += "\n\n"
    
    if errors:
        response += f"❌ **Ошибки:** {len(errors)}\n"
        response += "\n".join(errors[:5])
        if len(errors) > 5:
            response += f"\n... и ещё {len(errors) - 5}"
    
    if not response:
        response = "❌ Нет данных для обработки"
    
    return response


def process_excel_data_simple(df):
    """Простая обработка файла с игроками"""
    success = []
    errors = []
    
    # Ищем колонку с именами
    name_col = None
    for col in df.columns:
        col_lower = str(col).strip().lower()
        if col_lower in ['имя', 'name', 'игрок', 'player']:
            name_col = col
            break
    
    if not name_col:
        return "❌ Не найдена колонка с именами игроков!"
    
    for idx, row in df.iterrows():
        try:
            name = str(row[name_col]).strip()
            if not name or name == 'nan':
                continue
            
            # Проверяем, есть ли игрок
            cursor.execute("SELECT name FROM players WHERE name=?", (name,))
            exists = cursor.fetchone()
            
            if exists:
                success.append(f"✅ {name}: уже существует")
            else:
                cursor.execute("INSERT INTO players (name, rating) VALUES (?, 100)", (name,))
                conn.commit()
                success.append(f"✅ {name}: добавлен")
                
        except Exception as e:
            errors.append(f"❌ Строка {idx+2}: {str(e)}")
    
    response = ""
    if success:
        response += f"✅ **Обработано:** {len(success)}\n"
        response += "\n".join(success[:10])
    if errors:
        response += f"\n\n❌ **Ошибки:** {len(errors)}\n"
        response += "\n".join(errors[:5])
    
    return response

# ---------- ЗАПУСК БОТА ----------
if __name__ == "__main__":
    print("🤖 Бот запущен и готов к работе!")
    while True:
        try:
            bot.infinity_polling(timeout=10)
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            import time
            time.sleep(5)