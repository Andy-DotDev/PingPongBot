import asyncio
import math
import sqlite3
import os
from datetime import datetime
from typing import Optional, Tuple

import pandas as pd
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton,
    Message, CallbackQuery,
    FSInputFile  # Добавляем для отправки файлов
)
from aiogram.enums import ChatType  # <--- ИСПРАВЛЕНО!
from aiogram.utils.keyboard import InlineKeyboardBuilder
# ---------- КОНФИГУРАЦИЯ ----------
TOKEN = "8849779422:AAEs2xl47yvkBvJhpKcHcelhjV2LH8aueWk"
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

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

# ---------- СОСТОЯНИЯ FSM ----------
class RegistrationStates(StatesGroup):
    waiting_for_name = State()

class ResetStates(StatesGroup):
    waiting_for_player_name = State()
    waiting_for_stats_player = State()

class MatchStates(StatesGroup):
    waiting_for_opponent = State()
    waiting_for_winner = State()

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def get_player_by_id(telegram_id: int) -> Optional[Tuple[str, str]]:
    cursor.execute("SELECT name, role FROM players WHERE telegram_id=?", (telegram_id,))
    result = cursor.fetchone()
    return result if result else None

def get_rating(name: str) -> int:
    cursor.execute("SELECT rating FROM players WHERE name=?", (name,))
    result = cursor.fetchone()
    if result:
        return result[0]
    else:
        cursor.execute("INSERT INTO players (name, rating) VALUES (?, 100)", (name,))
        conn.commit()
        return 100

def has_role(telegram_id: int, required_role: str) -> bool:
    cursor.execute("SELECT role FROM players WHERE telegram_id=?", (telegram_id,))
    result = cursor.fetchone()
    if not result:
        return False
    role = result[0]
    roles = {'admin': 3, 'moderator': 2, 'user': 1, 'banned': 0}
    return roles.get(role, 0) >= roles.get(required_role, 0)

def update_elo(winner: str, loser: str, k: int = 32) -> Tuple[int, int]:
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

def register_player(telegram_id: int, name: str, role: str = 'user') -> bool:
    try:
        cursor.execute("INSERT INTO players (name, telegram_id, role) VALUES (?, ?, ?)", (name, telegram_id, role))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ГРУПП ----------
async def is_private_chat(message: Message) -> bool:
    """Проверяет, является ли чат личным"""
    return message.chat.type == ChatType.PRIVATE

async def is_group_chat(message: Message) -> bool:
    """Проверяет, является ли чат групповым"""
    return message.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]

async def check_registration(message: Message) -> bool:
    """Проверяет, зарегистрирован ли пользователь. Если нет - отправляет предупреждение."""
    telegram_id = message.from_user.id
    player = get_player_by_id(telegram_id)
    
    if not player:
        await message.answer(
            "❌ Вы не зарегистрированы!\n"
            "Пожалуйста, напишите боту в личные сообщения и нажмите '👤 Мой профиль'.",
            reply_markup=main_keyboard() if await is_private_chat(message) else None
        )
        return False
    return True

async def check_private_chat(message: Message) -> bool:
    """Проверяет, что это личный чат"""
    if not await is_private_chat(message):
        await message.answer(
            "🤖 Эта команда доступна только в личных сообщениях.\n"
            "Напишите мне в ЛС для использования всех функций.",
            reply_markup=types.ReplyKeyboardRemove()
        )
        return False
    return True


# ---------- КЛАВИАТУРА ДЛЯ ГРУППЫ ----------
def group_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для группы с командами"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Рейтинг")],
            [KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="❓ Помощь")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите команду..."
    )
    return keyboard

# ---------- КЛАВИАТУРА ДЛЯ ЛС ----------
def main_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню с кнопками (только для ЛС)"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏆 Рейтинг"), KeyboardButton(text="👤 Мой профиль")],
            [KeyboardButton(text="📝 Записать матч"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="❓ Помощь"), KeyboardButton(text="📥 Загрузить Excel")]
        ],
        resize_keyboard=True
    )
    return keyboard

# ---------- ХЕНДЛЕРЫ ----------
@dp.message(Command("start", "help"))
async def cmd_start(message: Message, state: FSMContext):
    """Обработка команды /start и /help"""
    await state.clear()
    
    telegram_id = message.from_user.id
    player = get_player_by_id(telegram_id)
    
    # Если это группа - показываем клавиатуру для группы
    if await is_group_chat(message):
        help_text = "🏓 **БОТ ДЛЯ УЧЁТА РЕЙТИНГА В НАСТОЛЬНЫЙ ТЕННИС**\n\n"
        help_text += "Используйте кнопки ниже!\n\n"
        help_text += "🔹 Для регистрации и записи матчей напишите боту в ЛС."
        
        await message.answer(
            help_text,
            parse_mode='Markdown',
            reply_markup=group_keyboard()
        )
        return
    
    # Для личных сообщений
    help_text = "🏓 **БОТ ДЛЯ УЧЁТА РЕЙТИНГА В НАСТОЛЬНЫЙ ТЕННИС**\n\n"
    help_text += "Используйте кнопки ниже для навигации!\n\n"
    
    if player:
        name, role = player
        help_text += f"✅ Вы авторизованы как: **{name}** (роль: {role})"
    else:
        help_text += "⚠️ Вы не зарегистрированы! Используйте кнопку '👤 Мой профиль'"
    
    await message.answer(help_text, parse_mode='Markdown', reply_markup=main_keyboard())

# ---------- КОМАНДЫ ДЛЯ ГРУППЫ ----------
@dp.message(Command("rating"))
async def group_rating(message: Message):
    """Показать рейтинг в группе (топ-10)"""
    cursor.execute("SELECT name, rating, games_played, games_won FROM players ORDER BY rating DESC LIMIT 10")
    data = cursor.fetchall()
    
    if not data:
        await message.answer("📊 Игроков пока нет!")
        return
    
    text = "🏆 **ТОП-10 РЕЙТИНГА**\n\n"
    for i, row in enumerate(data, 1):
        name, rating, games, wins = row
        winrate = round(wins/games*100, 1) if games > 0 else 0
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} {name} — **{rating}** pts (Игр: {games}, {winrate}%)\n"
    
    await message.answer(text, parse_mode='Markdown')

@dp.message(Command("profile"))
async def group_profile(message: Message):
    """Показать профиль игрока в группе"""
    args = message.text.split(maxsplit=1)
    
    if len(args) < 2:
        await message.answer(
            "❌ Укажите имя игрока!\n"
            "Пример: `/profile Андрей`",
            parse_mode='Markdown'
        )
        return
    
    name = args[1].strip()
    cursor.execute("SELECT name, rating, games_played, games_won FROM players WHERE name=?", (name,))
    player = cursor.fetchone()
    
    if not player:
        await message.answer(f"❌ Игрок '{name}' не найден!")
        return
    
    name, rating, games, wins = player
    winrate = round(wins/games*100, 1) if games > 0 else 0
    
    text = f"📊 **Профиль игрока:** {name}\n"
    text += f"🎯 Рейтинг: **{rating}**\n"
    text += f"📈 Игр сыграно: {games}\n"
    text += f"🏆 Побед: {wins} ({winrate}%)\n"
    
    await message.answer(text, parse_mode='Markdown')

@dp.message(Command("stats"))
async def group_stats(message: Message):
    """Показать общую статистику в группе"""
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
    
    text = "📊 **СТАТИСТИКА СИСТЕМЫ**\n\n"
    text += f"👥 Игроков: **{total_players}**\n"
    text += f"🏓 Матчей: **{total_matches}**\n"
    text += f"📈 Средний рейтинг: **{avg_rating}**\n"
    text += f"⬆️ Максимальный: **{max_rating}**\n"
    text += f"⬇️ Минимальный: **{min_rating}**\n"
    
    await message.answer(text, parse_mode='Markdown')

# ---------- ОБРАБОТЧИКИ КНОПОК ДЛЯ ГРУППЫ ----------
@dp.message(F.text == "📊 Рейтинг")
async def group_rating_button(message: Message):
    """Кнопка 'Рейтинг' в группе"""
    # Проверяем, что это группа
    if not await is_group_chat(message):
        # Если это ЛС - перенаправляем на основной обработчик
        await show_rating(message)
        return
    
    cursor.execute("SELECT name, rating, games_played, games_won FROM players ORDER BY rating DESC LIMIT 10")
    data = cursor.fetchall()
    
    if not data:
        await message.answer("📊 Игроков пока нет!", reply_markup=group_keyboard())
        return
    
    text = "🏆 **ТОП-10 РЕЙТИНГА**\n\n"
    for i, row in enumerate(data, 1):
        name, rating, games, wins = row
        winrate = round(wins/games*100, 1) if games > 0 else 0
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} {name} — **{rating}** pts (Игр: {games}, {winrate}%)\n"
    
    await message.answer(text, parse_mode='Markdown', reply_markup=group_keyboard())

@dp.message(F.text == "📈 Топ-10")
async def group_top10_button(message: Message):
    """Кнопка 'Топ-10' в группе"""
    # Просто вызываем тот же обработчик
    await group_rating_button(message)

@dp.message(F.text == "👤 Профиль")
async def group_profile_button(message: Message):
    """Кнопка 'Профиль' в группе"""
    if not await is_group_chat(message):
        await message.answer("❌ Эта кнопка работает только в группах!", reply_markup=main_keyboard())
        return
    
    await message.answer(
        "👤 **Введите имя игрока:**\n\n"
        "Пример: `Андрей`",
        parse_mode='Markdown',
        reply_markup=group_keyboard()
    )
    
    # Сохраняем состояние ожидания имени
    # Используем словарь для хранения ожидающих пользователей
    if not hasattr(group_profile_button, 'waiting_users'):
        group_profile_button.waiting_users = set()
    group_profile_button.waiting_users.add(message.from_user.id)
    
    @dp.message(lambda m: m.chat.id == message.chat.id and m.from_user.id in group_profile_button.waiting_users)
    async def get_profile_name(name_msg: Message):
        name = name_msg.text.strip()
        
        # Удаляем из ожидающих
        group_profile_button.waiting_users.discard(name_msg.from_user.id)
        
        cursor.execute("SELECT name, rating, games_played, games_won FROM players WHERE name=?", (name,))
        player = cursor.fetchone()
        
        if not player:
            await name_msg.answer(
                f"❌ Игрок '{name}' не найден!",
                reply_markup=group_keyboard()
            )
            return
        
        name, rating, games, wins = player
        winrate = round(wins/games*100, 1) if games > 0 else 0
        
        text = f"📊 **Профиль игрока:** {name}\n"
        text += f"🎯 Рейтинг: **{rating}**\n"
        text += f"📈 Игр сыграно: {games}\n"
        text += f"🏆 Побед: {wins} ({winrate}%)\n"
        
        await name_msg.answer(text, parse_mode='Markdown', reply_markup=group_keyboard())

@dp.message(F.text == "📊 Статистика")
async def group_stats_button(message: Message):
    """Кнопка 'Статистика' в группе"""
    if not await is_group_chat(message):
        await message.answer("❌ Эта кнопка работает только в группах!", reply_markup=main_keyboard())
        return
    
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
    
    text = "📊 **СТАТИСТИКА СИСТЕМЫ**\n\n"
    text += f"👥 Игроков: **{total_players}**\n"
    text += f"🏓 Матчей: **{total_matches}**\n"
    text += f"📈 Средний рейтинг: **{avg_rating}**\n"
    text += f"⬆️ Максимальный: **{max_rating}**\n"
    text += f"⬇️ Минимальный: **{min_rating}**\n\n"
    
    if top_players:
        text += "🏆 **Топ-3 игроков:**\n"
        medals = ["🥇", "🥈", "🥉"]
        for i, (name, rating) in enumerate(top_players):
            text += f"{medals[i]} {name} — **{rating}** pts\n"
    
    await message.answer(text, parse_mode='Markdown', reply_markup=group_keyboard())

# ---------- ОБРАБОТЧИК КНОПКИ "👤 Мой профиль" (только в ЛС) ----------
@dp.message(F.text == "👤 Мой профиль")
async def my_profile(message: Message, state: FSMContext):
    """Показать профиль или начать регистрацию (только в ЛС)"""
    # Проверяем, что это личный чат
    if not await check_private_chat(message):
        return
    
    telegram_id = message.from_user.id
    
    # Проверяем, не находится ли пользователь уже в процессе регистрации
    current_state = await state.get_state()
    if current_state == RegistrationStates.waiting_for_name.state:
        await message.answer(
            "⏳ Вы уже в процессе регистрации!\n"
            "Пожалуйста, введите ваше имя.",
            reply_markup=main_keyboard()
        )
        return
    
    player = get_player_by_id(telegram_id)
    
    if not player:
        await state.set_state(RegistrationStates.waiting_for_name)
        await message.answer(
            "❌ Вы не зарегистрированы!\nВведите ваше имя для регистрации:",
            reply_markup=main_keyboard()
        )
        return
    
    name, role = player
    cursor.execute("SELECT rating, games_played, games_won FROM players WHERE name=?", (name,))
    rating, games, wins = cursor.fetchone()
    winrate = round(wins/games*100, 1) if games > 0 else 0
    
    text = f"📊 **Профиль игрока:** {name}\n"
    text += f"🎯 Рейтинг: **{rating}**\n"
    text += f"📈 Игр сыграно: {games}\n"
    text += f"🏆 Побед: {wins} ({winrate}%)\n"
    text += f"👑 Роль: {role}"
    
    await message.answer(text, parse_mode='Markdown', reply_markup=main_keyboard())

@dp.message(RegistrationStates.waiting_for_name)
async def process_registration(message: Message, state: FSMContext):
    """Обработка ввода имени при регистрации (только в ЛС)"""
    # Проверяем, что это личный чат
    if not await check_private_chat(message):
        return
    
    telegram_id = message.from_user.id
    name = message.text.strip()
    
    # Проверяем, что пользователь ещё не зарегистрирован
    if get_player_by_id(telegram_id):
        await state.clear()
        await message.answer("❌ Вы уже зарегистрированы!", reply_markup=main_keyboard())
        return
    
    # Проверяем, что имя не пустое
    if not name or len(name) < 2:
        await message.answer(
            "❌ Имя не может быть пустым или слишком коротким!\n"
            "Пожалуйста, введите ваше имя (минимум 2 символа):",
            reply_markup=main_keyboard()
        )
        return
    
    # Проверяем, что имя не занято
    cursor.execute("SELECT name FROM players WHERE name=?", (name,))
    if cursor.fetchone():
        await message.answer(
            f"❌ Имя '{name}' уже занято! Попробуйте другое:",
            reply_markup=main_keyboard()
        )
        return
    
    # Регистрируем игрока
    if register_player(telegram_id, name, 'user'):
        await state.clear()
        await message.answer(
            f"✅ Поздравляю, {name}! Вы зарегистрированы!\n"
            f"Ваш начальный рейтинг: 100\n\n"
            f"🎯 Теперь вы можете использовать все функции бота!",
            reply_markup=main_keyboard()
        )
    else:
        await message.answer("❌ Ошибка регистрации", reply_markup=main_keyboard())

# ---------- ОБРАБОТЧИК КНОПКИ "🏆 Рейтинг" (только в ЛС) ----------
@dp.message(F.text == "🏆 Рейтинг")
async def show_rating(message: Message):
    """Показать таблицу рейтинга (только в ЛС)"""
    # Проверяем, что это личный чат
    if not await is_private_chat(message):
        # Если это группа - перенаправляем на групповой рейтинг
        await group_rating_button(message)
        return
    
    # Проверяем регистрацию
    if not await check_registration(message):
        return
    
    cursor.execute("SELECT name, rating, games_played, games_won FROM players ORDER BY rating DESC")
    data = cursor.fetchall()
    
    if not data:
        await message.answer("Игроков пока нет. Зарегистрируйтесь первым!", reply_markup=main_keyboard())
        return
    
    text = "🏆 **ТАБЛИЦА РЕЙТИНГА**\n\n"
    for i, row in enumerate(data[:20], 1):
        name, rating, games, wins = row
        winrate = round(wins/games*100, 1) if games > 0 else 0
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} {name} — **{rating}** pts (Игр: {games}, {winrate}%)\n"
    
    await message.answer(text, parse_mode='Markdown', reply_markup=main_keyboard())

def match_selection_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора способа записи матча"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Выбрать из списка", callback_data="match_select_from_list")],
            [InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="match_manual_input")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_match")]
        ]
    )
    return keyboard

# ---------- ОБРАБОТЧИКИ CALLBACK ДЛЯ ЗАПИСИ МАТЧА ----------

@dp.callback_query(F.data == "match_select_from_list")
async def match_select_from_list(callback: CallbackQuery, state: FSMContext):
    """Выбор соперника из списка"""
    player = get_player_by_id(callback.from_user.id)
    if not player:
        await callback.answer("❌ Вы не зарегистрированы!", show_alert=True)
        return
    
    cursor.execute("SELECT name FROM players WHERE name != ? ORDER BY rating DESC LIMIT 10", (player[0],))
    players = cursor.fetchall()
    
    if not players:
        await callback.answer("❌ Нет других игроков!", show_alert=True)
        return
    
    await state.set_state(MatchStates.waiting_for_opponent)
    await state.update_data(player1=player[0])
    
    text = "👥 **Выберите соперника:**\n(показаны топ-10 игроков)"
    keyboard = player_list_keyboard(players, "opponent")
    
    await callback.message.edit_text(text, parse_mode='Markdown', reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "match_manual_input")
async def match_manual_input(callback: CallbackQuery):
    """Ручной ввод матча"""
    await callback.message.edit_text(
        "✏️ **Введите матч в формате:**\n\n"
        "По ID: `/m 1 2 1`\n"
        "По имени: `/m Андрей Петя Андрей`\n\n"
        "💡 Для массового ввода: `/m 1 2 1 | 3 4 3`",
        parse_mode='Markdown'
    )
    await callback.answer()

@dp.callback_query(F.data == "cancel_match")
async def cancel_match(callback: CallbackQuery, state: FSMContext):
    """Отмена записи матча"""
    await state.clear()
    await callback.message.edit_text("❌ Запись матча отменена")
    await callback.answer()
    await callback.message.answer("🔙 Возврат в главное меню", reply_markup=main_keyboard())

@dp.callback_query(F.data.startswith("opponent_"))
async def select_opponent(callback: CallbackQuery, state: FSMContext):
    """Выбор соперника"""
    opponent = callback.data.replace("opponent_", "")
    data = await state.get_data()
    player1 = data.get('player1')
    
    if not player1:
        await callback.answer("❌ Ошибка! Начните заново.", show_alert=True)
        await state.clear()
        return
    
    await state.update_data(player2=opponent)
    await state.set_state(MatchStates.waiting_for_winner)
    
    text = f"✅ Соперник: {opponent}\n\nКто победил?"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🏆 {player1}", callback_data=f"win_{player1}_{opponent}")],
            [InlineKeyboardButton(text=f"🏆 {opponent}", callback_data=f"win_{opponent}_{player1}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_match")]
        ]
    )
    
    await callback.message.edit_text(text, parse_mode='Markdown', reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("win_"))
async def process_winner(callback: CallbackQuery, state: FSMContext):
    """Обработка победителя"""
    parts = callback.data.split("_")
    winner = parts[1]
    loser = parts[2]
    
    cursor.execute("SELECT name FROM players WHERE name=?", (winner,))
    if not cursor.fetchone():
        await callback.answer("❌ Игрок не найден!", show_alert=True)
        return
    
    # Сохраняем старые значения ДО обновления
    old_winner = get_rating(winner)
    old_loser = get_rating(loser)
    
    new_w, new_l = update_elo(winner, loser)
    
    winner_diff = new_w - old_winner
    loser_diff = new_l - old_loser
    
    text = f"✅ **Матч записан!**\n\n"
    text += f"🏆 {winner}: {new_w} (+{winner_diff})\n"
    text += f"📉 {loser}: {new_l} ({loser_diff})"
    
    await state.clear()
    await callback.message.edit_text(text, parse_mode='Markdown')
    await callback.answer("✅ Матч записан!")
    await callback.message.answer(f"🎉 Поздравляем {winner} с победой!", reply_markup=main_keyboard())

def player_list_keyboard(players: list, action: str, exclude: Optional[str] = None) -> InlineKeyboardMarkup:
    """Клавиатура со списком игроков"""
    builder = InlineKeyboardBuilder()
    
    for player in players[:10]:
        name = player[0]
        if exclude and name == exclude:
            continue
        builder.button(text=name, callback_data=f"{action}_{name}")
    
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(2)
    return builder.as_markup()
# ---------- ОБРАБОТЧИК КНОПКИ "📝 Записать матч" (только в ЛС) ----------
@dp.message(F.text == "📝 Записать матч")
async def start_match(message: Message, state: FSMContext):
    """Начать запись матча (только в ЛС)"""
    # Проверяем, что это личный чат
    if not await check_private_chat(message):
        return
    
    # Проверяем регистрацию
    if not await check_registration(message):
        return
    
    telegram_id = message.from_user.id
    player = get_player_by_id(telegram_id)
    
    if player[1] == 'banned':
        await message.answer("⛔ Вы забанены и не можете участвовать!", reply_markup=main_keyboard())
        return
    
    if not has_role(telegram_id, 'moderator'):
        await message.answer(
            "⛔ Только модераторы и администраторы могут записывать матчи!\n"
            "Обратитесь к администратору для получения прав.",
            reply_markup=main_keyboard()
        )
        return
    
    await message.answer(
        "📝 **Как хотите записать матч?**\n\n"
        "• **Выбрать из списка** — выбрать игроков из топ-10\n"
        "• **Ввести вручную** — ввести имена игроков и победителя\n"
        "(например: /m Андрей Петя Андрей)\n\n"
        "💡 Также доступен массовый ввод: `/m 1 2 1 | 2 3 3`",
        parse_mode='Markdown',
        reply_markup=match_selection_keyboard()
    )


# ---------- ОБРАБОТЧИК КНОПКИ "📊 Статистика" (только в ЛС) ----------
@dp.message(F.text == "📊 Статистика")
async def show_statistics(message: Message):
    """Показать общую статистику (только в ЛС)"""
    # Проверяем, что это личный чат
    if not await check_private_chat(message):
        return
    
    # Проверяем регистрацию
    if not await check_registration(message):
        return
    
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
    
    # Активные игроки
    cursor.execute("SELECT name, games_played FROM players ORDER BY games_played DESC LIMIT 3")
    active_players = cursor.fetchall()
    
    # Самые результативные
    cursor.execute("SELECT name, games_won FROM players ORDER BY games_won DESC LIMIT 3")
    best_winners = cursor.fetchall()
    
    # Статистика по ролям
    cursor.execute("SELECT role, COUNT(*) FROM players GROUP BY role")
    roles_stats = cursor.fetchall()
    
    text = "📊 **ОБЩАЯ СТАТИСТИКА СИСТЕМЫ**\n\n"
    
    text += "📌 **Общая информация:**\n"
    text += f"👥 Всего игроков: **{total_players}**\n"
    text += f"🏓 Всего матчей: **{total_matches}**\n"
    text += f"📈 Средний рейтинг: **{avg_rating}**\n"
    text += f"⬆️ Максимальный рейтинг: **{max_rating}**\n"
    text += f"⬇️ Минимальный рейтинг: **{min_rating}**\n\n"
    
    if top_players:
        text += "🏆 **Топ-3 игроков:**\n"
        medals = ["🥇", "🥈", "🥉"]
        for i, (name, rating) in enumerate(top_players):
            text += f"{medals[i]} {name} — **{rating}** pts\n"
        text += "\n"
    
    if active_players and active_players[0][1] > 0:
        text += "🔥 **Самые активные игроки:**\n"
        for i, (name, games) in enumerate(active_players[:3], 1):
            text += f"{i}. {name} — {games} игр\n"
        text += "\n"
    
    if best_winners and best_winners[0][1] > 0:
        text += "💪 **Самые результативные:**\n"
        for i, (name, wins) in enumerate(best_winners[:3], 1):
            cursor.execute("SELECT games_played FROM players WHERE name=?", (name,))
            games = cursor.fetchone()[0]
            winrate = round(wins / games * 100, 1) if games > 0 else 0
            text += f"{i}. {name} — {wins} побед ({winrate}%)\n"
        text += "\n"
    
    if roles_stats:
        text += "👑 **Распределение ролей:**\n"
        role_emojis = {'admin': '👑', 'moderator': '🔨', 'user': '👤', 'banned': '⛔'}
        for role, count in roles_stats:
            emoji = role_emojis.get(role, '❓')
            text += f"{emoji} {role}: {count}\n"
    
    await message.answer(text, parse_mode='Markdown', reply_markup=main_keyboard())

# ---------- ОБРАБОТЧИК КНОПКИ "📥 Загрузить Excel" (только в ЛС) ----------
@dp.message(F.text == "📥 Загрузить Excel")
async def upload_excel_button(message: Message):
    """Кнопка загрузки Excel (только в ЛС)"""
    # Проверяем, что это личный чат
    if not await check_private_chat(message):
        return
    
    # Проверяем регистрацию
    if not await check_registration(message):
        return
    
    telegram_id = message.from_user.id
    
    if not has_role(telegram_id, 'moderator'):
        await message.answer("⛔ У вас нет прав!", reply_markup=main_keyboard())
        return
    
    await message.answer(
        "📥 **Загрузка Excel-файла**\n\n"
        "Отправьте Excel-файл (.xlsx или .xls) с колонками:\n\n"
        "📌 **Для матчей:** Игрок1, Игрок2, Победитель\n"
        "📌 **Для игроков:** Имя, Рейтинг\n\n"
        "Все игроки должны быть зарегистрированы!",
        parse_mode='Markdown',
        reply_markup=main_keyboard()
    )

# ---------- ОБРАБОТЧИК КНОПКИ "❓ Помощь" ----------
@dp.message(F.text == "❓ Помощь")
async def help_button(message: Message, state: FSMContext):
    """Обработка кнопки '❓ Помощь'"""
    await cmd_start(message, state)

# ---------- КОМАНДА /m ДЛЯ БЫСТРОГО ВВОДА ----------
@dp.message(Command("m"))
async def manual_match(message: Message):
    """Быстрый ввод матчей (только в ЛС)"""
    # Проверяем, что это личный чат
    if not await check_private_chat(message):
        return
    
    telegram_id = message.from_user.id
    
    if not has_role(telegram_id, 'moderator'):
        await message.answer(
            "⛔ Только модераторы и администраторы могут записывать матчи!",
            reply_markup=main_keyboard()
        )
        return
    
    text = message.text.replace('/m', '').strip()
    
    if '|' in text:
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
            
            result = await process_single_match(parts, message)
            if result:
                results.append(result)
            else:
                errors.append(f"❌ Ошибка в матче: '{match_str}'")
        
        response = "📊 **РЕЗУЛЬТАТЫ МАССОВОГО ВВОДА**\n\n"
        
        if results:
            response += "✅ **Успешно записано:**\n"
            response += "\n".join(results)
            response += "\n\n"
        
        if errors:
            response += "❌ **Ошибки:**\n"
            response += "\n".join(errors)
        
        await message.answer(response, parse_mode='Markdown', reply_markup=main_keyboard())
        
    else:
        parts = text.split()
        if len(parts) < 3:
            await message.answer(
                "❌ **Неверный формат!**\n\n"
                "По ID: `/m 1 2 1` (ID игрока1, ID игрока2, ID победителя)\n"
                "По имени: `/m Андрей Петя Андрей`\n"
                "Массово: `/m 1 2 1 | 3 4 3 | 5 6 5`\n\n"
                "💡 Чтобы узнать ID игрока, используйте `/players`",
                parse_mode='Markdown',
                reply_markup=main_keyboard()
            )
            return
        
        result = await process_single_match(parts, message)
        if result:
            await message.answer(result, parse_mode='Markdown', reply_markup=main_keyboard())
        else:
            await message.answer("❌ Ошибка при записи матча!", reply_markup=main_keyboard())

async def process_single_match(parts: list, message: Message) -> Optional[str]:
    """Обработка одного матча"""
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
    
    # Сохраняем старые значения ДО обновления
    old_p1 = get_rating(p1)
    old_p2 = get_rating(p2)
    
    if winner == p1:
        new1, new2 = update_elo(p1, p2)
        diff_p1 = new1 - old_p1
        diff_p2 = new2 - old_p2
        
        response = f"✅ **{p1}** vs **{p2}**\n"
        response += f"🏆 {p1}: {new1} (+{diff_p1})\n"
        response += f"📉 {p2}: {new2} ({diff_p2})"
    else:
        new1, new2 = update_elo(p2, p1)
        diff_p1 = new1 - old_p1
        diff_p2 = new2 - old_p2
        
        response = f"✅ **{p1}** vs **{p2}**\n"
        response += f"🏆 {p2}: {new1} (+{diff_p1})\n"
        response += f"📉 {p1}: {new2} ({diff_p2})"
    
    return response

# ---------- ОСТАЛЬНЫЕ ОБРАБОТЧИКИ (callback, reset, excel и т.д.) ----------
# ---------- ФУНКЦИИ ДЛЯ ОБРАБОТКИ EXCEL ----------

def process_excel_matches(df, found_columns):
    """Обработка Excel-файла с матчами"""
    success = []
    errors = []
    total_rows = 0
    
    # Находим колонки
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
    
    if not all([p1_col, p2_col, win_col]):
        return "❌ Не найдены все необходимые колонки!\n\nТребуются: Игрок1, Игрок2, Победитель"
    
    for idx, row in df.iterrows():
        total_rows += 1
        try:
            p1 = str(row[p1_col]).strip()
            p2 = str(row[p2_col]).strip()
            winner = str(row[win_col]).strip()
            
            # Пропускаем пустые строки
            if not p1 or not p2 or not winner:
                errors.append(f"⚠️ Строка {idx+2}: пропущена (пустые данные)")
                continue
            if p1 == 'nan' or p2 == 'nan' or winner == 'nan':
                errors.append(f"⚠️ Строка {idx+2}: пропущена (пустые ячейки)")
                continue
            
            # Проверка на одинаковых игроков
            if p1 == p2:
                errors.append(f"❌ Строка {idx+2}: игроки должны быть разными! ({p1} = {p2})")
                continue
            
            # Проверка победителя
            if winner not in [p1, p2]:
                errors.append(f"❌ Строка {idx+2}: победитель '{winner}' не является игроком!")
                continue
            
            # Проверка существования игроков в БД
            cursor.execute("SELECT name FROM players WHERE name=?", (p1,))
            if not cursor.fetchone():
                errors.append(f"❌ Строка {idx+2}: игрок '{p1}' не найден в БД! Зарегистрируйте его.")
                continue
            
            cursor.execute("SELECT name FROM players WHERE name=?", (p2,))
            if not cursor.fetchone():
                errors.append(f"❌ Строка {idx+2}: игрок '{p2}' не найден в БД! Зарегистрируйте его.")
                continue
            
            # Проверка на баны
            cursor.execute("SELECT role FROM players WHERE name=?", (winner,))
            role = cursor.fetchone()
            if role and role[0] == 'banned':
                errors.append(f"❌ Строка {idx+2}: игрок {winner} забанен!")
                continue
            
            # Запись матча
            old_p1 = get_rating(p1)
            old_p2 = get_rating(p2)
            
            if winner == p1:
                new_w, new_l = update_elo(p1, p2)
                diff_p1 = new_w - old_p1
                diff_p2 = new_l - old_p2
                success.append(f"✅ {p1} победил {p2} (+{diff_p1} pts)")
            else:
                new_w, new_l = update_elo(p2, p1)
                diff_p1 = new_w - old_p1
                diff_p2 = new_l - old_p2
                success.append(f"✅ {p2} победил {p1} (+{diff_p1} pts)")
                
        except Exception as e:
            errors.append(f"❌ Строка {idx+2}: {str(e)}")
    
    # Формируем ответ
    response = f"📊 **Обработано строк:** {total_rows}\n\n"
    
    if success:
        response += f"✅ **Успешно записано:** {len(success)} матчей\n"
        response += "\n".join(success[:10])
        if len(success) > 10:
            response += f"\n... и ещё {len(success) - 10} матчей"
        response += "\n\n"
    
    if errors:
        response += f"❌ **Ошибки:** {len(errors)}\n"
        response += "\n".join(errors[:5])
        if len(errors) > 5:
            response += f"\n... и ещё {len(errors) - 5} ошибок"
    
    if not success and not errors:
        response = "❌ Нет данных для обработки"
    
    return response

def process_excel_players(df):
    """Обработка Excel-файла с игроками"""
    success = []
    errors = []
    total_rows = 0
    updated = 0
    
    # Ищем колонки
    name_col = None
    rating_col = None
    
    for col in df.columns:
        col_lower = str(col).strip().lower()
        if col_lower in ['имя', 'name', 'игрок', 'player']:
            name_col = col
        elif col_lower in ['рейтинг', 'rating', 'pts', 'points']:
            rating_col = col
    
    if not name_col:
        return "❌ Не найдена колонка с именами игроков!\n\nТребуется колонка: Имя, Name, Игрок или Player"
    
    for idx, row in df.iterrows():
        total_rows += 1
        try:
            name = str(row[name_col]).strip()
            if not name or name == 'nan':
                errors.append(f"⚠️ Строка {idx+2}: пропущена (пустое имя)")
                continue
            
            # Проверяем, существует ли игрок
            cursor.execute("SELECT name, rating FROM players WHERE name=?", (name,))
            exists = cursor.fetchone()
            
            # Если есть колонка с рейтингом
            if rating_col:
                try:
                    rating = int(row[rating_col]) if pd.notna(row[rating_col]) else 100
                except:
                    rating = 100
            else:
                rating = 100
            
            if exists:
                # Обновляем существующего игрока
                cursor.execute("UPDATE players SET rating=? WHERE name=?", (rating, name))
                conn.commit()
                updated += 1
                success.append(f"✅ {name}: обновлён (рейтинг {rating})")
            else:
                # Добавляем нового игрока
                cursor.execute("INSERT INTO players (name, rating) VALUES (?, ?)", (name, rating))
                conn.commit()
                success.append(f"✅ {name}: добавлен (рейтинг {rating})")
                
        except Exception as e:
            errors.append(f"❌ Строка {idx+2}: {str(e)}")
    
    # Формируем ответ
    response = f"📊 **Обработано строк:** {total_rows}\n\n"
    
    if success:
        response += f"✅ **Успешно обработано:** {len(success)}\n"
        response += f"   — Новых игроков: {len(success) - updated}\n"
        response += f"   — Обновлено: {updated}\n\n"
        response += "\n".join(success[:10])
        if len(success) > 10:
            response += f"\n... и ещё {len(success) - 10}"
        response += "\n\n"
    
    if errors:
        response += f"❌ **Ошибки:** {len(errors)}\n"
        response += "\n".join(errors[:5])
        if len(errors) > 5:
            response += f"\n... и ещё {len(errors) - 5} ошибок"
    
    if not success and not errors:
        response = "❌ Нет данных для обработки"
    
    return response
# ---------- ОБРАБОТЧИК EXCEL-ФАЙЛОВ ----------
@dp.message(F.document)
async def handle_excel_file(message: Message):
    """Обработка загруженных Excel-файлов"""
    # Проверяем, что это личный чат
    if not await is_private_chat(message):
        await message.answer("❌ Загрузка Excel доступна только в ЛС!")
        return
    
    telegram_id = message.from_user.id
    
    # Проверяем права
    if not has_role(telegram_id, 'moderator'):
        await message.answer("⛔ Только модераторы и администраторы могут загружать Excel!", reply_markup=main_keyboard())
        return
    
    file = message.document
    file_name = file.file_name
    
    # Проверяем расширение
    if not file_name.endswith(('.xlsx', '.xls')):
        await message.answer(
            "❌ Неверный формат! Нужен .xlsx или .xls файл.\n"
            "Пожалуйста, отправьте файл с правильным расширением.",
            reply_markup=main_keyboard()
        )
        return
    
    status_msg = await message.answer("🔄 Загружаю и обрабатываю файл... Подождите...")
    
    try:
        # Скачиваем файл
        file_info = await bot.get_file(file.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        
        # Сохраняем временный файл
        temp_file = f"temp_{file_name}"
        with open(temp_file, "wb") as f:
            f.write(downloaded_file.getvalue())
        
        # Читаем Excel
        try:
            df = pd.read_excel(temp_file, engine='openpyxl')
        except:
            df = pd.read_excel(temp_file, engine='xlrd')
        
        os.remove(temp_file)  # Удаляем временный файл
        
        if df.empty:
            await status_msg.delete()
            await message.answer("❌ В файле нет данных!", reply_markup=main_keyboard())
            return
        
        # Определяем тип файла
        columns = df.columns.tolist()
        found_columns = []
        
        for col in columns:
            col_lower = str(col).strip().lower()
            if 'игрок1' in col_lower or 'player1' in col_lower:
                found_columns.append(('Игрок1', col))
            elif 'игрок2' in col_lower or 'player2' in col_lower:
                found_columns.append(('Игрок2', col))
            elif 'победитель' in col_lower or 'winner' in col_lower:
                found_columns.append(('Победитель', col))
        
        # Обрабатываем в зависимости от типа
        if len(found_columns) >= 3:
            # Это файл с матчами
            results = process_excel_matches(df, found_columns)
            response = "🏓 **РЕЗУЛЬТАТЫ ЗАГРУЗКИ МАТЧЕЙ**\n\n" + results
        else:
            # Это файл с игроками
            results = process_excel_players(df)
            response = "📊 **РЕЗУЛЬТАТЫ ЗАГРУЗКИ ИГРОКОВ**\n\n" + results
        
        await status_msg.delete()
        await message.answer(response, parse_mode='Markdown', reply_markup=main_keyboard())
        
    except Exception as e:
        await status_msg.delete()
        await message.answer(
            f"❌ Ошибка при обработке файла:\n\n{str(e)}\n\n"
            "Убедитесь, что файл не повреждён и имеет правильную структуру.",
            reply_markup=main_keyboard()
        )
# ---------- ОБРАБОТЧИК НЕИЗВЕСТНЫХ СООБЩЕНИЙ ----------
@dp.message(F.text)
async def handle_unknown(message: Message, state: FSMContext):
    """Обработка неизвестных текстовых сообщений"""
    # Если это группа - не отвечаем на случайные сообщения
    if await is_group_chat(message):
        # Проверяем, не является ли сообщение командой
        if message.text.startswith('/'):
            return
        # Не отвечаем в группе на случайные сообщения
        return
    
    current_state = await state.get_state()
    
    if current_state:
        await message.answer(
            "⏳ Вы находитесь в процессе операции. Следуйте инструкциям бота.",
            reply_markup=main_keyboard()
        )
    else:
        await message.answer(
            "❓ Используйте кнопки меню для навигации.",
            reply_markup=main_keyboard()
        )

# ---------- ОБРАБОТЧИК ДЛЯ УПОМИНАНИЙ В ГРУППЕ ----------
@dp.message(F.text, lambda message: message.chat.type in ["group", "supergroup"])
async def handle_group_mention(message: Message):
    """Обработка упоминаний бота в группе"""
    if f"@{bot.username}" in message.text:
        await message.answer(
            "🤖 Привет! Я бот для учёта рейтинга в настольный теннис.\n\n"
            "📌 **Доступные команды в группе:**\n"
            "/rating - топ-10 рейтинга\n"
            "/profile Имя - профиль игрока\n"
            "/stats - общая статистика\n\n"
            "🔹 Для полного функционала напишите мне в ЛС.",
            reply_markup=types.ReplyKeyboardRemove()
        )

# ---------- ЗАПУСК БОТА ----------
async def main():
    print("🤖 Бот запущен и готов к работе!")
    try:
        await dp.start_polling(bot)
    except asyncio.CancelledError:
        print("👋 Бот остановлен")
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен пользователем")
    finally:
        await bot.session.close()
        conn.close()
        print("✅ Соединения закрыты")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")