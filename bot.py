import discord
from discord import app_commands
from discord.ext import commands
import datetime
import re
import json
import os
import asyncio
from typing import Optional

from config import (
    DISCORD_TOKEN, CHANNELS, ROLES, ROLES_TO_REMOVE,
    STAT_ROLES, STAFF_ROLES, ACCEPT_ROLES, POSITION_TO_ROLE, ALL_POSITIONS,
    POSITION_ORDER
)
from sheets import SheetsManager

# Инициализация бота
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

# Инициализация Google Sheets (только для наказаний и статистики)
sheets = SheetsManager()

# ===== ФУНКЦИЯ НОРМАЛИЗАЦИИ НИКА =====
def normalize_nick(nick: str) -> str:
    """Очищает ник от приписок типа [x901-101], (x901), {x901} и т.д."""
    if not nick:
        return nick
    nick = re.sub(r'\[[^\]]*\]', '', nick)
    nick = re.sub(r'\([^)]*\)', '', nick)
    nick = re.sub(r'\{[^}]*\}', '', nick)
    return nick.strip()

# ===== ФАЙЛ ДЛЯ ХРАНЕНИЯ СОСТАВА =====
STAFF_FILE = "staff_data.json"

def load_staff():
    if os.path.exists(STAFF_FILE):
        try:
            with open(STAFF_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []
    return []

def save_staff():
    try:
        with open(STAFF_FILE, 'w', encoding='utf-8') as f:
            json.dump(staff_list, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения состава: {e}")

# ===== СОСТАВ В ПАМЯТИ =====
staff_list = load_staff()
staff_message_id = None
staff_channel_id = CHANNELS['состав']

# ===== ПРОВЕРКА ПРАВ =====
def has_staff_role(interaction: discord.Interaction):
    for role in interaction.user.roles:
        if role.id in STAFF_ROLES:
            return True
    return False

def has_stat_role(interaction: discord.Interaction):
    for role in interaction.user.roles:
        if role.id in STAT_ROLES:
            return True
    return False

def has_staff_role_check():
    async def predicate(interaction: discord.Interaction):
        if not has_staff_role(interaction):
            await interaction.response.send_message(
                "❌ У вас нет доступа к этой команде. Требуется роль: Зам. Куратора и выше.",
                ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)

def has_stat_role_check():
    async def predicate(interaction: discord.Interaction):
        if not has_stat_role(interaction):
            await interaction.response.send_message(
                "❌ У вас нет доступа к этой команде. Требуется КП-роль.",
                ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)

# ============ КОМАНДЫ ============

@bot.event
async def on_ready():
    print(f'✅ Бот {bot.user} запущен!')
    print(f'📋 Загружено {len(staff_list)} сотрудников в составе')
    await bot.tree.sync()
    print('✅ Команды синхронизированы')

# 1. /stat
@bot.tree.command(name="stat", description="Статистика игрока")
@app_commands.describe(user="Пользователь, чью статистику показать")
@has_stat_role_check()
async def stat(interaction: discord.Interaction, user: discord.Member):
    try:
        await interaction.response.defer(thinking=True)
        
        # Пробуем найти по нику
        clean_nick = normalize_nick(user.display_name)
        user_data = sheets.find_user_by_nick_fuzzy(clean_nick)
        
        if not user_data:
            # Если не нашли — пробуем найти по display_name (с приписками)
            print(f"⚠️ Поиск по {clean_nick} не дал результатов, пробуем по {user.display_name}")
            user_data = sheets.find_user_by_nick(user.display_name)
        
        if not user_data:
            await interaction.followup.send(
                f"❌ Пользователь {user.mention} не найден в таблице.\n"
                f"Проверь, что ник в таблице соответствует твоему Discord-нику.\n"
                f"Твой ник: `{user.display_name}`\n"
                f"Очищенный ник: `{clean_nick}`"
            )
            return
        
        embed = discord.Embed(
            title=f"📊 Статистика {user.display_name}",
            color=discord.Color.blue()
        )
        embed.add_field(name="☘ Должность", value=user_data['position'], inline=True)
        embed.add_field(name="❀ Баллы", value=user_data['points'], inline=True)
        embed.add_field(name="☠ Варны", value=user_data['warns'], inline=True)
        embed.add_field(name="☠ Устники", value=user_data['warnings'], inline=True)
        embed.add_field(name="✉ Почта", value=user_data['email'] or 'Не указана', inline=True)
        embed.set_footer(text=f"ID: {user.id} | Найден как: {user_data['nick']}")
        await interaction.followup.send(embed=embed)
        
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для stat истекло")
    except Exception as e:
        print(f"❌ Ошибка в stat: {e}")
        await interaction.followup.send(f"❌ Ошибка: {e}")

# 2. /варн
@bot.tree.command(name="варн", description="Выдать предупреждение игроку")
@app_commands.describe(
    user="Игрок",
    reason="Причина предупреждения",
    count="Количество варнов (по умолчанию 1)"
)
@has_staff_role_check()
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str, count: Optional[int] = 1):
    try:
        await interaction.response.defer(thinking=True)
        
        if count < 1:
            await interaction.followup.send("❌ Количество должно быть больше 0.")
            return
        
        clean_nick = normalize_nick(user.display_name)
        user_data = sheets.find_user_by_nick(clean_nick)
        if not user_data:
            await interaction.followup.send(f"❌ Пользователь {user.mention} не найден в таблице.")
            return
        
        current_count = user_data['warns_count']
        new_count = current_count + count
        
        if new_count >= 3:
            new_warns = '3/3'
            sheets.update_user(user_data['row'], 4, new_warns)
            await remove_user(interaction, user, f"3/3 предупреждений (причина: {reason}, выдано {count} шт.)")
            return
        else:
            new_warns = f'{new_count}/3'
        
        sheets.update_user(user_data['row'], 4, new_warns)
        
        channel = bot.get_channel(CHANNELS['наказания'])
        embed = discord.Embed(
            title="⚠️ Выдано предупреждение",
            color=discord.Color.orange(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="Игрок", value=user.mention, inline=True)
        embed.add_field(name="Должность", value=user_data['position'], inline=True)
        embed.add_field(name="Причина", value=reason, inline=True)
        embed.add_field(name="Кол-во", value=f"+{count}", inline=True)
        embed.add_field(name="Выдал", value=interaction.user.mention, inline=True)
        embed.add_field(name="Варны", value=f"{new_warns}", inline=True)
        embed.set_footer(text=f"ID: {user.id}")
        if channel:
            await channel.send(embed=embed)
        await interaction.followup.send(f"✅ Выдано {count} варн(а) {user.mention} ({new_warns})")
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для варн истекло")
    except Exception as e:
        print(f"❌ Ошибка в варн: {e}")

# 3. /устник
@bot.tree.command(name="устник", description="Выдать устное предупреждение")
@app_commands.describe(
    user="Игрок",
    reason="Причина",
    count="Количество устников (по умолчанию 1)"
)
@has_staff_role_check()
async def warning(interaction: discord.Interaction, user: discord.Member, reason: str, count: int = 1):
    try:
        await interaction.response.defer(thinking=True)
        
        if count < 1:
            await interaction.followup.send("❌ Количество должно быть больше 0.")
            return
        
        clean_nick = normalize_nick(user.display_name)
        user_data = sheets.find_user_by_nick(clean_nick)
        if not user_data:
            await interaction.followup.send(f"❌ Пользователь {user.mention} не найден в таблице.")
            return
        
        current_warnings = user_data['warnings_count']
        current_warns = user_data['warns_count']
        
        # Добавляем устники
        new_warnings = current_warnings + count
        conversions = new_warnings // 3
        remaining_warnings = new_warnings % 3
        
        row = user_data['row']
        
        # Обновляем таблицу (один раз)
        if conversions > 0:
            new_warns = current_warns + conversions
            if new_warns >= 3:
                sheets.update_user(row, 4, '3/3')
                sheets.update_user(row, 5, f'{remaining_warnings}/3')
                # Даём время на запись в таблицу
                await asyncio.sleep(0.5)
                await remove_user(interaction, user, f"3/3 предупреждений ({conversions}x 3 устника → варн, причина: {reason})")
                return
            else:
                new_warns_str = f'{new_warns}/3'
                sheets.update_user(row, 4, new_warns_str)
                sheets.update_user(row, 5, f'{remaining_warnings}/3')
        else:
            new_warnings_str = f'{new_warnings}/3'
            sheets.update_user(row, 5, new_warnings_str)
        
        # Даём время на запись в таблицу
        await asyncio.sleep(0.5)
        
        # Получаем обновлённые данные для сообщения
        updated_data = sheets.get_user_data(row)
        
        channel = bot.get_channel(CHANNELS['наказания'])
        embed = discord.Embed(
            title="💬 Выдано устное предупреждение",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="Игрок", value=user.mention, inline=True)
        embed.add_field(name="Должность", value=updated_data['position'], inline=True)
        embed.add_field(name="Причина", value=reason, inline=True)
        embed.add_field(name="Кол-во", value=f"+{count}", inline=True)
        embed.add_field(name="Выдал", value=interaction.user.mention, inline=True)
        embed.add_field(name="Устники", value=updated_data['warnings'], inline=True)
        if conversions > 0:
            embed.add_field(name="Варны", value=updated_data['warns'], inline=True)
        if channel:
            await channel.send(embed=embed)
        
        await interaction.followup.send(f"✅ Выдано {count} устн(ых) {user.mention}")
        
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для устник истекло")
    except Exception as e:
        print(f"❌ Ошибка в устник: {e}")

# 4. /снятьварн
@bot.tree.command(name="снятьварн", description="Снять одно предупреждение за 100 баллов")
@app_commands.describe(user="Игрок")
@has_staff_role_check()
async def remove_warn(interaction: discord.Interaction, user: discord.Member):
    try:
        await interaction.response.defer(thinking=True)
        clean_nick = normalize_nick(user.display_name)
        
        # Используем метод remove_penalty из sheets.py
        result = sheets.remove_penalty(clean_nick, 'варн')
        
        await interaction.followup.send(result['message'])
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для снятьварн истекло")
    except Exception as e:
        print(f"❌ Ошибка в снятьварн: {e}")

# 5. /снятьустник
@bot.tree.command(name="снятьустник", description="Снять одно устное предупреждение за 50 баллов")
@app_commands.describe(user="Игрок")
@has_staff_role_check()
async def remove_warning(interaction: discord.Interaction, user: discord.Member):
    try:
        await interaction.response.defer(thinking=True)
        clean_nick = normalize_nick(user.display_name)
        
        # Используем новый метод remove_penalty
        result = sheets.remove_penalty(clean_nick, 'устник')
        
        await interaction.followup.send(result['message'])
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для снятьустник истекло")
    except Exception as e:
        print(f"❌ Ошибка в снятьустник: {e}")

# 6. /снять
@bot.tree.command(name="снять", description="Снять сотрудника (все роли, удалить доступ к таблице и форме)")
@app_commands.describe(user="Игрок", reason="Причина снятия")
@has_staff_role_check()
async def remove_staff(interaction: discord.Interaction, user: discord.Member, reason: str):
    try:
        await interaction.response.defer(thinking=True)
        await remove_user(interaction, user, reason)
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для снять истекло")
    except Exception as e:
        print(f"❌ Ошибка в снять: {e}")

async def remove_user(interaction, user: discord.Member, reason: str):
    global staff_list
    try:
        clean_nick = normalize_nick(user.display_name)
        user_data = sheets.find_user_by_nick(clean_nick)
        if not user_data:
            await interaction.followup.send(f"❌ Пользователь {user.mention} не найден в таблице.")
            return

        # Получаем email для удаления доступа
        email = user_data.get('email', '')
        
        # Удаляем из состава
        staff_list = [m for m in staff_list if m['nick'] != clean_nick]
        save_staff()

        # Снимаем роли
        removed_roles = []
        for role in user.roles:
            if role.id in ROLES_TO_REMOVE:
                try:
                    await user.remove_roles(role)
                    removed_roles.append(role.name)
                except:
                    pass
        
        # Удаляем доступ к таблице и форме (если есть email)
        access_message = ""
        if email:
            result = sheets.full_remove_user(email)
            if result['success']:
                access_message = f"\n\n🔐 {result['message']}"
            else:
                access_message = f"\n\n⚠️ {result['message']}"
        else:
            access_message = "\n\n⚠️ Email не указан, доступ не удалён."

        await update_staff_message()

        channel = bot.get_channel(CHANNELS['учет_снятых'])
        embed = discord.Embed(
            title="🚫 Снятие сотрудника",
            color=discord.Color.red(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="1. Discord ID", value=user.mention, inline=False)
        embed.add_field(name="2. Ник", value=user.display_name, inline=False)
        embed.add_field(name="3. Причина", value=reason, inline=False)
        embed.add_field(name="4. Дата", value=datetime.datetime.now().strftime('%d.%m.%Y'), inline=False)
        embed.add_field(name="5. Должность", value=user_data['position'], inline=False)
        embed.set_footer(text=f"Снял: {interaction.user.display_name}")
        if channel:
            await channel.send(embed=embed)
        
        await interaction.followup.send(f"✅ Сотрудник {user.mention} снят. Причина: {reason}{access_message}")
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие в remove_user истекло")
    except Exception as e:
        print(f"❌ Ошибка в remove_user: {e}")

# 7. /повысить
@bot.tree.command(name="повысить", description="Повысить сотрудника")
@app_commands.describe(nick="Ник сотрудника (без @)", new_position="Новая должность")
@has_staff_role_check()
async def promote(interaction: discord.Interaction, nick: str, new_position: str):
    try:
        await interaction.response.defer(thinking=True)
        
        if new_position not in ALL_POSITIONS:
            positions_list = "\n".join(ALL_POSITIONS)
            await interaction.followup.send(f"❌ Должность '{new_position}' не найдена.\nДоступные должности:\n{positions_list}")
            return
        
        clean_nick = normalize_nick(nick)
        existing = next((m for m in staff_list if m['nick'] == clean_nick), None)
        if not existing:
            await interaction.followup.send(f"❌ {nick} не найден в составе.")
            return
        
        old_position = existing['position']
        existing['position'] = new_position
        save_staff()
        
        # Ищем пользователя в Discord по нику
        user = None
        for member in interaction.guild.members:
            if normalize_nick(member.display_name) == clean_nick:
                user = member
                break
        
        # Меняем роли, если пользователь найден
        if user:
            old_role_id = POSITION_TO_ROLE.get(old_position)
            if old_role_id:
                old_role = interaction.guild.get_role(old_role_id)
                if old_role and old_role in user.roles:
                    await user.remove_roles(old_role)
            
            new_role_id = POSITION_TO_ROLE.get(new_position)
            if new_role_id:
                new_role = interaction.guild.get_role(new_role_id)
                if new_role:
                    await user.add_roles(new_role)
            
            user_mention = user.mention
        else:
            user_mention = clean_nick
            print(f"⚠️ Пользователь {clean_nick} не найден на сервере, роли не менялись")
        
        # Отправляем уведомление в канал учёта
        channel = bot.get_channel(CHANNELS['учет_принятых_повышенных'])
        embed = discord.Embed(
            title="📈 Сотрудник повышен",
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="1. Пинг сотрудника", value=user_mention, inline=False)
        embed.add_field(name="2. Ник", value=clean_nick, inline=False)
        embed.add_field(name="3. Повышен на должность", value=f"[{new_position}]", inline=False)
        embed.add_field(name="4. Кто повысил", value=interaction.user.mention, inline=False)
        embed.set_footer(text=f"Было: {old_position} → Стало: {new_position}")
        if channel:
            await channel.send(embed=embed)
        
        await update_staff_message()
        await interaction.followup.send(f"✅ {nick} повышен до {new_position}")
        
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для повысить истекло")
    except Exception as e:
        print(f"❌ Ошибка в повысить: {e}")
        await interaction.followup.send(f"❌ Ошибка: {e}")

# 8. /принять
@bot.tree.command(name="принять", description="Принять нового сотрудника в состав")
@app_commands.describe(
    user="Игрок",
    position="Должность (Стажер или Мл. Поддержка для обзванивающих, остальные - только для зам. куратора+)"
)
async def accept_staff(interaction: discord.Interaction, user: discord.Member, position: str):
    try:
        await interaction.response.defer(thinking=True)
        
        # Проверяем, существует ли должность
        if position not in ALL_POSITIONS:
            positions_list = "\n".join(ALL_POSITIONS)
            await interaction.followup.send(f"❌ Должность '{position}' не найдена.\nДоступные должности:\n{positions_list}")
            return
        
        # Получаем роли пользователя
        user_roles = [role.id for role in interaction.user.roles]
        
        # Проверяем, есть ли у пользователя доступ к команде (Обзванивающий или Зам. Куратора+)
        has_accept_permission = any(role in ACCEPT_ROLES for role in user_roles)
        if not has_accept_permission:
            await interaction.followup.send(
                "❌ У вас нет доступа к этой команде.\n"
                "Требуется роль: **Обзванивающий** или **Зам. Куратора+**."
            )
            return
        
        # Проверяем, есть ли у пользователя полный доступ (Зам. Куратора+)
        has_full_access = any(role in STAFF_ROLES for role in user_roles)
        
        # Ограничения для обзванивающих
        allowed_positions = ['Стажер', 'Мл. Поддержка']
        if not has_full_access and position not in allowed_positions:
            await interaction.followup.send(
                f"❌ У вас нет прав для приёма на должность '{position}'.\n"
                f"Ваша роль **Обзванивающий** может принимать только на: {', '.join(allowed_positions)}\n"
                f"Для приёма на другие должности нужна роль **Зам. Куратора** или выше."
            )
            return
        
        # Проверяем, есть ли уже в составе
        clean_nick = normalize_nick(user.display_name)
        existing = next((m for m in staff_list if m['nick'] == clean_nick), None)
        if existing:
            await interaction.followup.send(f"❌ {user.mention} уже есть в составе.")
            return
        
        # Добавляем в состав
        staff_list.append({'nick': clean_nick, 'position': position})
        save_staff()
        
        # Выдаём роли
        role_id = POSITION_TO_ROLE.get(position)
        if role_id:
            role = interaction.guild.get_role(role_id)
            if role:
                await user.add_roles(role)
        
        staff_role = interaction.guild.get_role(ROLES['STAFF_FT'])
        if staff_role:
            await user.add_roles(staff_role)
        
        # ===== 1. УВЕДОМЛЕНИЕ В КАНАЛ УЧЁТА (разное для Стажер и остальных) =====
        channel = bot.get_channel(CHANNELS['учет_принятых_повышенных'])
        if channel:
            # Определяем заголовок в зависимости от должности
            if position == 'Стажер':
                title = "📥 Принят новый сотрудник"
                color = discord.Color.green()
            else:
                title = "📈 Сотрудник повышен"
                color = discord.Color.gold()
            
            embed = discord.Embed(
                title=title,
                color=color,
                timestamp=datetime.datetime.now()
            )
            embed.add_field(name="1. Пинг сотрудника", value=user.mention, inline=False)
            embed.add_field(name="2. Ник", value=user.display_name, inline=False)
            embed.add_field(name="3. Принят на должность", value=f"[{position}]", inline=False)
            embed.add_field(name="4. Кто провел обзвон", value=interaction.user.mention, inline=False)
            await channel.send(embed=embed)
        
        # ===== 2. ПРИВЕТСТВИЕ В ОБЫЧНЫЙ ЧАТ (ID: 1297555288790011977) =====
        try:
            welcome_channel = bot.get_channel(1297555288790011977)
            if welcome_channel:
                welcome_message = (
                    f"{user.mention}, добро пожаловать! 🎉\n"
                    f"Скинь свою почту <@&1327267237777768532> или <@&1311678066845679616>."
                )
                await welcome_channel.send(welcome_message)
                print(f"✅ Приветствие отправлено в канал {welcome_channel.name}")
            else:
                print(f"⚠️ Канал с ID 1297555288790011977 не найден!")
        except Exception as e:
            print(f"❌ Ошибка при отправке приветствия: {e}")
        
        # Обновляем состав
        await update_staff_message()
        await interaction.followup.send(f"✅ {user.mention} принят на должность {position}")
        
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для принять истекло")
    except Exception as e:
        print(f"❌ Ошибка в принять: {e}")
        await interaction.followup.send(f"❌ Ошибка: {e}")

# 9. /удалитьсостав
@bot.tree.command(name="удалитьсостав", description="Удалить сотрудника из состава вручную (роли НЕ снимаются)")
@app_commands.describe(nick="Ник сотрудника (без @)")
@has_staff_role_check()
async def remove_staff_manual(interaction: discord.Interaction, nick: str):
    try:
        await interaction.response.defer(thinking=True)
        global staff_list
        
        clean_nick = normalize_nick(nick)
        existing = next((m for m in staff_list if m['nick'] == clean_nick), None)
        if not existing:
            await interaction.followup.send(f"❌ {nick} не найден в составе.")
            return
        
        staff_list = [m for m in staff_list if m['nick'] != clean_nick]
        save_staff()
        
        await update_staff_message()
        await interaction.followup.send(f"✅ {nick} удалён из состава (роли не сняты)")
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для удалитьсостав истекло")
    except Exception as e:
        print(f"❌ Ошибка в удалитьсостав: {e}")
        await interaction.followup.send(f"❌ Ошибка: {e}")

# 10. /обновитьсостав
@bot.tree.command(name="обновитьсостав", description="Принудительно обновить сообщение с составом")
@has_staff_role_check()
async def refresh_staff(interaction: discord.Interaction):
    try:
        await interaction.response.defer(thinking=True)
        global staff_message_id
        
        # Сбрасываем ID сообщения, чтобы бот создал новое
        staff_message_id = None
        
        # Обновляем состав
        await update_staff_message()
        
        await interaction.followup.send("✅ Состав успешно обновлён!")
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для обновитьсостав истекло")
    except Exception as e:
        print(f"❌ Ошибка в обновитьсостав: {e}")

# 11. /help
@bot.tree.command(name="help", description="Показать список команд и информацию о боте")
async def help_command(interaction: discord.Interaction):
    try:
        embed = discord.Embed(
            title="🤖 ArtyStaff Bot",
            description="Бот для автоматизации управления персоналом FT",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="📊 Статистика",
            value="`/stat @ник` - Показать статистику игрока",
            inline=False
        )
        embed.add_field(
            name="⚖️ Наказания",
            value=(
                "`/варн @ник причина [кол-во]` - Выдать варн(ы)\n"
                "`/устник @ник причина [кол-во]` - Выдать устник(и)\n"
                "`/снятьварн @ник` - Снять варн за 100 баллов\n"
                "`/снятьустник @ник` - Снять устник за 50 баллов"
            ),
            inline=False
        )
        embed.add_field(
            name="👥 Управление составом",
            value=(
                "`/принять @ник должность` - Принять нового сотрудника\n"
                "`/снять @ник причина` - Снять сотрудника\n"
                "`/повысить ник должность` - Повысить сотрудника\n"
                "`/добавитьсостав ник должность` - Добавить в состав (без уведомлений)\n"
                "`/удалитьсостав ник` - Удалить из состава (роли НЕ снимаются)\n"
                "`/обновитьсостав` - Обновить сообщение с составом"
            ),
            inline=False
        )
        embed.add_field(
            name="📌 Доступ к командам",
            value=(
                "**Обзванивающий:** `/принять` (только Стажер и Мл. Поддержка)\n"
                "**Зам. Куратора+:** Все команды управления составом\n"
                "**Все КП:** `/stat`"
            ),
            inline=False
        )
        embed.add_field(
            name="📋 Система баллов",
            value=(
                "Снятие устника - **50 баллов**\n"
                "Снятие варна - **100 баллов**"
            ),
            inline=False
        )
        embed.set_footer(text="ArtyStaff Bot | ArtyGrief")
        
        await interaction.response.send_message(embed=embed)
        
    except Exception as e:
        print(f"❌ Ошибка в help: {e}")
        await interaction.response.send_message(f"❌ Ошибка: {e}")

# 12. /добавитьсостав
@bot.tree.command(name="добавитьсостав", description="Добавить сотрудника в состав вручную (ник без @)")
@app_commands.describe(nick="Ник сотрудника (без @)", position="Должность")
@has_staff_role_check()
async def add_staff_manual(interaction: discord.Interaction, nick: str, position: str):
    try:
        await interaction.response.defer(thinking=True)
        
        if position not in ALL_POSITIONS:
            positions_list = "\n".join(ALL_POSITIONS)
            await interaction.followup.send(f"❌ Должность '{position}' не найдена.\nДоступные должности:\n{positions_list}")
            return
        
        clean_nick = normalize_nick(nick)
        existing = next((m for m in staff_list if m['nick'] == clean_nick), None)
        if existing:
            await interaction.followup.send(f"❌ {nick} уже есть в составе.")
            return
        
        staff_list.append({'nick': clean_nick, 'position': position})
        save_staff()
        
        await update_staff_message()
        await interaction.followup.send(f"✅ {nick} добавлен в состав как {position}")
        
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для добавитьсостав истекло")
    except Exception as e:
        print(f"❌ Ошибка в добавитьсостав: {e}")
        await interaction.followup.send(f"❌ Ошибка: {e}")

# 13. /отгул
@bot.tree.command(name="отгул", description="Оформить отгул сотруднику")
@app_commands.describe(
    user="Игрок",
    date="Дата отгула (например: 08.09)"
)
@has_staff_role_check()
async def take_off(interaction: discord.Interaction, user: discord.Member, date: str):
    try:
        await interaction.response.defer(thinking=True)
        
        clean_nick = normalize_nick(user.display_name)
        user_data = sheets.find_user_by_nick(clean_nick)
        
        if not user_data:
            await interaction.followup.send(f"❌ Пользователь {user.mention} не найден в таблице.")
            return
        
        row = user_data['row']  # Строка сотрудника
        
        # ===== 1. Находим колонку с датой =====
        date_col = sheets.find_column_by_date(date)
        
        if not date_col:
            await interaction.followup.send(f"❌ Дата {date} не найдена в таблице.")
            return
        
        # ===== 2. Копируем значение и стиль из B80 =====
        source_row = 80
        source_col = 2  # B
        
        target_row = row
        target_col = date_col
        
        copy_success = sheets.copy_cell_style_with_value(
            source_row, source_col,
            target_row, target_col
        )
        
        if not copy_success:
            await interaction.followup.send("❌ Ошибка при копировании отгула.")
            return
        
        # ===== 3. Снимаем баллы (40) =====
        points_col = 3  # Колонка C
        current_points = int(user_data['points']) if user_data['points'].isdigit() else 0
        new_points = current_points - 40
        
        if new_points < 0:
            new_points = 0
        
        sheets.update_user(row, points_col, str(new_points))
        
        # ===== 4. Отправляем уведомление =====
        channel = bot.get_channel(CHANNELS['наказания'])
        embed = discord.Embed(
            title="📅 Оформлен отгул",
            color=discord.Color.orange(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="Игрок", value=user.mention, inline=True)
        embed.add_field(name="Дата", value=date, inline=True)
        embed.add_field(name="Списано баллов", value="40", inline=True)
        embed.add_field(name="Остаток баллов", value=str(new_points), inline=True)
        embed.add_field(name="Выдал", value=interaction.user.mention, inline=True)
        embed.set_footer(text=f"ID: {user.id}")
        if channel:
            await channel.send(embed=embed)
        
        await interaction.followup.send(
            f"✅ Отгул оформлен {user.mention} на {date}\n"
            f"Списано 40 баллов. Остаток: {new_points}"
        )
        
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для отгул истекло")
    except Exception as e:
        print(f"❌ Ошибка в отгул: {e}")
        await interaction.followup.send(f"❌ Ошибка: {e}")

# ============ АВТОМАТИЧЕСКОЕ ОБНОВЛЕНИЕ СОСТАВА ============

async def update_staff_message():
    global staff_message_id, staff_list

    channel = bot.get_channel(staff_channel_id)
    if not channel:
        return

    staff_list.sort(key=lambda x: POSITION_ORDER.get(x['position'], 99))

    if not staff_list:
        content = "📋 СОСТАВ FT\n\nСотрудников пока нет"
    else:
        # Формируем список сотрудников в тройных кавычках
        staff_text = ""
        for member in staff_list:
            staff_text += f"• {member['nick']} — {member['position']}\n"
        
        # Формируем полное сообщение
        content = f"""📋 СОСТАВ FT

```\n{staff_text}```

Общий состав: {len(staff_list)} человек

Нашли ошибку? Тег <@&1327267237777768532>

Таблица сотрудников:
https://docs.google.com/spreadsheets/d/1-3ER99-RpUkPdeRE4JC5s0KRnV1unqNnmNQhtf4J7a4/edit?pli=1&gid=624912206#gid=624912206

<@&1328055611853635636>"""

    try:
        if staff_message_id:
            msg = await channel.fetch_message(staff_message_id)
            await msg.edit(content=content)
        else:
            async for msg in channel.history(limit=100):
                if msg.author == bot.user and msg.content.startswith('📋 СОСТАВ FT'):
                    staff_message_id = msg.id
                    await msg.edit(content=content)
                    return
            msg = await channel.send(content)
            staff_message_id = msg.id

        save_staff()

    except Exception as e:
        print(f"Ошибка обновления состава: {e}")

# ===== ОБРАБОТКА СООБЩЕНИЙ В КАНАЛЕ УЧЁТА =====

def parse_line(line):
    line = line.strip()
    if '. ' in line:
        return line.split('. ', 1)[1] if '. ' in line else line
    elif ') ' in line:
        return line.split(') ', 1)[1] if ') ' in line else line
    elif '. ' in line:
        return line.split('.', 1)[1].strip() if '.' in line else line
    return line

def extract_discord_id(text):
    match = re.search(r'<@(\d+)>', text)
    if match:
        return int(match.group(1))
    return None

def extract_position(text):
    match = re.search(r'\[([^\]]+)\]', text)
    if match:
        return match.group(1).strip()
    if 'Принят на должность' in text:
        return text.split('Принят на должность', 1)[1].strip()
    if 'Повышен на должность' in text:
        return text.split('Повышен на должность', 1)[1].strip()
    return None

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    if message.channel.id == CHANNELS['учет_принятых_повышенных']:
        content = message.content
        lines = content.split('\n')
        
        if len(lines) < 3:
            return
        
        try:
            full_text = " ".join(lines).lower()
            
            if 'принят' in full_text:
                await handle_acceptance(message, lines)
            elif 'повышен' in full_text:
                await handle_promotion(message, lines)
                
        except Exception as e:
            print(f"❌ Ошибка обработки сообщения в канале учёта: {e}")
    
    elif message.channel.id == CHANNELS['учет_снятых']:
        content = message.content
        lines = content.split('\n')
        
        if len(lines) < 3:
            return
        
        try:
            await handle_removal_from_channel(message, lines)
        except Exception as e:
            print(f"❌ Ошибка обработки сообщения в канале снятых: {e}")

async def handle_acceptance(message, lines):
    line1 = parse_line(lines[0]) if len(lines) > 0 else ""
    line2 = parse_line(lines[1]) if len(lines) > 1 else ""
    line3 = parse_line(lines[2]) if len(lines) > 2 else ""
    
    user_id = extract_discord_id(line1)
    if not user_id:
        user_id = extract_discord_id(line2)
    
    if not user_id:
        print("⚠️ Не удалось найти Discord ID")
        return
    
    user = message.guild.get_member(user_id)
    if not user:
        print(f"⚠️ Пользователь {user_id} не найден на сервере")
        return
    
    nick = line2 if line2 and not line2.startswith('<@') else user.display_name
    if not nick or nick.startswith('<@'):
        nick = user.display_name
    
    clean_nick = normalize_nick(nick)
    position = extract_position(line3)
    if not position:
        print("⚠️ Не удалось определить должность")
        return
    
    existing = next((m for m in staff_list if m['nick'] == clean_nick), None)
    if not existing:
        staff_list.append({'nick': clean_nick, 'position': position})
        save_staff()
    
    role_id = POSITION_TO_ROLE.get(position)
    if role_id:
        role = message.guild.get_role(role_id)
        if role:
            await user.add_roles(role)
    
    staff_role = message.guild.get_role(ROLES['STAFF_FT'])
    if staff_role:
        await user.add_roles(staff_role)
    
    await update_staff_message()
    print(f"✅ Принят: {clean_nick} ({position})")

async def handle_promotion(message, lines):
    line1 = parse_line(lines[0]) if len(lines) > 0 else ""
    line2 = parse_line(lines[1]) if len(lines) > 1 else ""
    line3 = parse_line(lines[2]) if len(lines) > 2 else ""
    
    user_id = extract_discord_id(line1)
    if not user_id:
        user_id = extract_discord_id(line2)
    
    if not user_id:
        print("⚠️ Не удалось найти Discord ID")
        return
    
    user = message.guild.get_member(user_id)
    if not user:
        print(f"⚠️ Пользователь {user_id} не найден на сервере")
        return
    
    nick = line2 if line2 and not line2.startswith('<@') else user.display_name
    if not nick or nick.startswith('<@'):
        nick = user.display_name
    
    clean_nick = normalize_nick(nick)
    new_position = extract_position(line3)
    if not new_position:
        print("⚠️ Не удалось определить новую должность")
        return
    
    for member in staff_list:
        if member['nick'] == clean_nick:
            old_position = member['position']
            member['position'] = new_position
            save_staff()
            
            old_role_id = POSITION_TO_ROLE.get(old_position)
            if old_role_id:
                old_role = message.guild.get_role(old_role_id)
                if old_role and old_role in user.roles:
                    await user.remove_roles(old_role)
            
            new_role_id = POSITION_TO_ROLE.get(new_position)
            if new_role_id:
                new_role = message.guild.get_role(new_role_id)
                if new_role:
                    await user.add_roles(new_role)
            break
    
    await update_staff_message()
    print(f"✅ Повышен: {clean_nick} → {new_position}")

async def handle_removal_from_channel(message, lines):
    global staff_list
    
    line1 = parse_line(lines[0]) if len(lines) > 0 else ""
    line2 = parse_line(lines[1]) if len(lines) > 1 else ""
    
    user_id = extract_discord_id(line1)
    if not user_id:
        user_id = extract_discord_id(line2)
    
    if not user_id:
        print("⚠️ Не удалось найти Discord ID при снятии")
        return
    
    user = message.guild.get_member(user_id)
    if not user:
        print(f"⚠️ Пользователь {user_id} не найден на сервере")
        return
    
    nick = line2 if line2 and not line2.startswith('<@') else user.display_name
    if not nick or nick.startswith('<@'):
        nick = user.display_name
    
    clean_nick = normalize_nick(nick)
    
    staff_list = [m for m in staff_list if m['nick'] != clean_nick]
    save_staff()
    
    for role in user.roles:
        if role.id in ROLES_TO_REMOVE:
            try:
                await user.remove_roles(role)
            except:
                pass
    
    await update_staff_message()
    print(f"✅ Снят: {clean_nick}")

# Запуск бота
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
