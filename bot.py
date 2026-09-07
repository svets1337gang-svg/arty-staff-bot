import discord
from discord import app_commands
from discord.ext import commands
import datetime
import re
import json
import os
import asyncio
import threading
from typing import Optional
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from config import (
    DISCORD_TOKEN, CHANNELS, ROLES, ROLES_TO_REMOVE,
    STAT_ROLES, STAFF_ROLES, ACCEPT_ROLES, POSITION_TO_ROLE, ALL_POSITIONS,
    POSITION_ORDER, GOOGLE_SHEETS_ID, GUILD_ID, CREDENTIALS_FILE, GOOGLE_SCOPES,
    PROMOTION_LADDER,
)
from sheets import SheetsManager

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix='!', intents=intents)

sheets = SheetsManager()
GUILD = discord.Object(id=GUILD_ID)


def get_google_service():
    """Создаёт и возвращает Google Drive сервис"""
    try:
        creds = Credentials.from_service_account_file(
            CREDENTIALS_FILE,
            scopes=GOOGLE_SCOPES
        )
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        print(f"❌ Ошибка создания Google сервиса: {e}")
        return None


def normalize_nick(nick: str) -> str:
    """Очищает ник от приписок типа [x901-101], (x901), {x901} и т.д."""
    if not nick:
        return nick
    nick = re.sub(r'\[[^\]]*\]', '', nick)
    nick = re.sub(r'\([^)]*\)', '', nick)
    nick = re.sub(r'\{[^}]*\}', '', nick)
    return nick.strip()


STAFF_FILE = "staff_data.json"


def load_staff():
    if os.path.exists(STAFF_FILE):
        try:
            with open(STAFF_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_staff():
    try:
        with open(STAFF_FILE, 'w', encoding='utf-8') as f:
            json.dump(staff_list, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения состава: {e}")


staff_list = load_staff()
staff_message_id = None
staff_channel_id = CHANNELS['состав']


def user_role_ids(user) -> set:
    return {role.id for role in getattr(user, 'roles', [])}


def has_staff_role(interaction: discord.Interaction) -> bool:
    return bool(user_role_ids(interaction.user) & set(STAFF_ROLES))


def has_stat_role(interaction: discord.Interaction) -> bool:
    return bool(user_role_ids(interaction.user) & set(STAT_ROLES))


def has_accept_role(interaction: discord.Interaction) -> bool:
    return bool(user_role_ids(interaction.user) & set(ACCEPT_ROLES))


def is_punishment_channel(interaction: discord.Interaction) -> bool:
    return interaction.channel_id == CHANNELS['наказания']


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


def has_accept_role_check():
    async def predicate(interaction: discord.Interaction):
        if not has_accept_role(interaction):
            await interaction.response.send_message(
                "❌ У вас нет доступа к этой команде.\n"
                "Требуется роль: **Обзванивающий** или **Зам. Куратора+**.",
                ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


def punishment_channel_only():
    async def predicate(interaction: discord.Interaction):
        if not is_punishment_channel(interaction):
            await interaction.response.send_message(
                "❌ Эта команда работает **только** в канале наказаний.\n"
                "Исключение: `/stat` можно использовать в любом канале.",
                ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


async def position_autocomplete(interaction: discord.Interaction, current: str):
    current_lower = (current or "").lower()
    results = [
        app_commands.Choice(name=pos, value=pos)
        for pos in ALL_POSITIONS
        if current_lower in pos.lower()
    ]
    return results[:25]


async def send_command_error(interaction: discord.Interaction, text: str):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except Exception:
        pass


# ============ КОМАНДЫ ============

@bot.event
async def on_ready():
    print(f'✅ Бот {bot.user} запущен!')
    print(f'📋 Загружено {len(staff_list)} сотрудников в составе')
    print(f'🔑 Google credentials: {CREDENTIALS_FILE}')

    try:
        bot.tree.copy_global_to(guild=GUILD)
        synced = await bot.tree.sync(guild=GUILD)
        print(f'✅ Синхронизировано команд на сервере: {len(synced)}')
        for cmd in synced:
            print(f'   - /{cmd.name}')
        print('📋 Команды синхронизированы на сервере (гильдия, без ожидания Discord)')
    except Exception as e:
        print(f'❌ Ошибка синхронизации команд: {e}')


@bot.tree.command(name="stat", description="Статистика игрока")
@app_commands.describe(user="Пользователь, чью статистику показать")
@has_stat_role_check()
async def stat(interaction: discord.Interaction, user: discord.Member):
    try:
        await interaction.response.defer()
        clean_nick = normalize_nick(user.display_name)
        user_data = sheets.find_user_by_nick_fuzzy(clean_nick)
        if not user_data:
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
        await send_command_error(interaction, f"❌ Ошибка: {e}")


@bot.tree.command(name="варн", description="Выдать предупреждение игроку")
@app_commands.describe(
    user="Игрок",
    reason="Причина предупреждения",
    count="Количество варнов (по умолчанию 1)"
)
@has_staff_role_check()
@punishment_channel_only()
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str, count: Optional[int] = 1):
    try:
        await interaction.response.defer()
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
        await send_command_error(interaction, f"❌ Ошибка: {e}")


@bot.tree.command(name="устник", description="Выдать устное предупреждение")
@app_commands.describe(
    user="Игрок",
    reason="Причина",
    count="Количество устников (по умолчанию 1)"
)
@has_staff_role_check()
@punishment_channel_only()
async def warning(interaction: discord.Interaction, user: discord.Member, reason: str, count: int = 1):
    try:
        await interaction.response.defer()
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
        new_warnings = current_warnings + count
        conversions = new_warnings // 3
        remaining_warnings = new_warnings % 3
        row = user_data['row']
        if conversions > 0:
            new_warns = current_warns + conversions
            if new_warns >= 3:
                sheets.update_user(row, 4, '3/3')
                sheets.update_user(row, 5, f'{remaining_warnings}/3')
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
        await asyncio.sleep(0.5)
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
        await send_command_error(interaction, f"❌ Ошибка: {e}")


@bot.tree.command(name="снятьварн", description="Снять одно предупреждение за 500 баллов")
@app_commands.describe(user="Игрок")
@has_staff_role_check()
@punishment_channel_only()
async def remove_warn(interaction: discord.Interaction, user: discord.Member):
    try:
        await interaction.response.defer()
        clean_nick = normalize_nick(user.display_name)
        result = sheets.remove_penalty(clean_nick, 'варн')
        await interaction.followup.send(result['message'])
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для снятьварн истекло")
    except Exception as e:
        print(f"❌ Ошибка в снятьварн: {e}")
        await send_command_error(interaction, f"❌ Ошибка: {e}")


@bot.tree.command(name="снятьустник", description="Снять одно устное предупреждение за 175 баллов")
@app_commands.describe(user="Игрок")
@has_staff_role_check()
@punishment_channel_only()
async def remove_warning(interaction: discord.Interaction, user: discord.Member):
    try:
        await interaction.response.defer()
        clean_nick = normalize_nick(user.display_name)
        result = sheets.remove_penalty(clean_nick, 'устник')
        await interaction.followup.send(result['message'])
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для снятьустник истекло")
    except Exception as e:
        print(f"❌ Ошибка в снятьустник: {e}")
        await send_command_error(interaction, f"❌ Ошибка: {e}")


@bot.tree.command(name="снять", description="Снять сотрудника (все роли, удалить доступ к таблице и форме)")
@app_commands.describe(user="Игрок", reason="Причина снятия")
@has_staff_role_check()
@punishment_channel_only()
async def remove_staff(interaction: discord.Interaction, user: discord.Member, reason: str):
    try:
        await interaction.response.defer()
        await remove_user(interaction, user, reason)
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для снять истекло")
    except Exception as e:
        print(f"❌ Ошибка в снять: {e}")
        await send_command_error(interaction, f"❌ Ошибка: {e}")


async def remove_user(interaction, user: discord.Member, reason: str):
    global staff_list
    try:
        clean_nick = normalize_nick(user.display_name)
        user_data = sheets.find_user_by_nick(clean_nick)
        if not user_data:
            await interaction.followup.send(f"❌ Пользователь {user.mention} не найден в таблице.")
            return
        email = user_data.get('email', '')
        staff_list = [m for m in staff_list if normalize_nick(m['nick']).lower() != clean_nick.lower()]
        save_staff()
        for role in user.roles:
            if role.id in ROLES_TO_REMOVE:
                try:
                    await user.remove_roles(role)
                except Exception:
                    pass
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
        await send_command_error(interaction, f"❌ Ошибка: {e}")


async def swap_position_roles(guild: discord.Guild, user: discord.Member, old_position: str, new_position: str):
    """Снимает только должностную роль, STAFF FT не трогает."""
    old_role_id = POSITION_TO_ROLE.get(old_position)
    if old_role_id and old_role_id != ROLES['STAFF_FT']:
        old_role = guild.get_role(old_role_id)
        if old_role and old_role in user.roles:
            await user.remove_roles(old_role)

    new_role_id = POSITION_TO_ROLE.get(new_position)
    if new_role_id:
        new_role = guild.get_role(new_role_id)
        if new_role:
            await user.add_roles(new_role)

    obzvon_role = guild.get_role(ROLES['Обзвон_FT'])
    if obzvon_role and obzvon_role in user.roles:
        await user.remove_roles(obzvon_role)


@bot.tree.command(name="повысить", description="Повысить сотрудника на должность (на ступень выше)")
@app_commands.describe(
    user="Сотрудник, которого нужно повысить",
    new_position="Новая должность"
)
@app_commands.autocomplete(new_position=position_autocomplete)
@has_accept_role_check()
@punishment_channel_only()
async def promote(
    interaction: discord.Interaction,
    user: discord.Member,
    new_position: str
):
    try:
        await interaction.response.defer()

        if new_position not in ALL_POSITIONS:
            positions_list = "\n".join(ALL_POSITIONS)
            await interaction.followup.send(
                f"❌ Должность `{new_position}` не найдена.\n"
                f"Доступные должности:\n{positions_list}"
            )
            return

        clean_nick = normalize_nick(user.display_name)
        existing = next(
            (
                m for m in staff_list
                if normalize_nick(m['nick']).lower() == clean_nick.lower()
            ),
            None
        )

        if not existing:
            await interaction.followup.send(
                f"❌ Пользователь {user.mention} не найден в составе.\n"
                f"Ник: `{clean_nick}`"
            )
            return

        old_position = existing['position']

        if old_position == new_position:
            await interaction.followup.send(
                f"❌ У {user.mention} уже стоит должность `{new_position}`."
            )
            return

        full_access = has_staff_role(interaction)
        if not full_access:
            if not (old_position == 'Стажер' and new_position == 'Мл. Поддержка'):
                await interaction.followup.send(
                    "❌ Роль **Обзванивающий** может повышать только "
                    "**Стажер → Мл. Поддержка**.\n"
                    "Для остальных повышений нужна роль **Зам. Куратора+**."
                )
                return
        else:
            if old_position in PROMOTION_LADDER and new_position in PROMOTION_LADDER:
                old_idx = PROMOTION_LADDER.index(old_position)
                new_idx = PROMOTION_LADDER.index(new_position)
                if new_idx <= old_idx:
                    await interaction.followup.send(
                        f"❌ `{new_position}` не является повышением относительно `{old_position}`."
                    )
                    return

        existing['position'] = new_position
        save_staff()

        try:
            await swap_position_roles(interaction.guild, user, old_position, new_position)
        except Exception as e:
            print(f"⚠️ Ошибка смены ролей при повышении: {e}")

        channel = bot.get_channel(CHANNELS['учет_принятых_повышенных'])
        embed = discord.Embed(
            title="📈 Сотрудник повышен",
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="1. Пинг сотрудника", value=user.mention, inline=False)
        embed.add_field(name="2. Ник", value=clean_nick, inline=False)
        embed.add_field(name="3. Повышен на должность", value=f"[{new_position}]", inline=False)
        embed.add_field(name="4. Кто повысил", value=interaction.user.mention, inline=False)
        embed.set_footer(text=f"Было: {old_position} → Стало: {new_position}")
        if channel:
            await channel.send(embed=embed)

        await update_staff_message()
        await interaction.followup.send(
            f"✅ {user.mention} повышен с **{old_position}** до **{new_position}**."
        )

    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для повысить истекло")
    except Exception as e:
        print(f"❌ Ошибка в повысить: {e}")
        await send_command_error(interaction, f"❌ Ошибка: {e}")


@bot.tree.command(name="принять", description="Принять нового сотрудника на должность Стажер")
@app_commands.describe(user="Игрок")
@has_accept_role_check()
@punishment_channel_only()
async def accept_staff(interaction: discord.Interaction, user: discord.Member):
    try:
        await interaction.response.defer()
        position = 'Стажер'
        clean_nick = normalize_nick(user.display_name)
        existing = next(
            (
                m for m in staff_list
                if normalize_nick(m['nick']).lower() == clean_nick.lower()
            ),
            None
        )
        if existing:
            await interaction.followup.send(f"❌ {user.mention} уже есть в составе.")
            return
        staff_list.append({'nick': clean_nick, 'position': position})
        save_staff()

        intern_role = interaction.guild.get_role(ROLES['Стажер'])
        if intern_role:
            await user.add_roles(intern_role)
            print(f"✅ Выдана роль: {intern_role.name}")

        staff_role = interaction.guild.get_role(ROLES['STAFF_FT'])
        if staff_role:
            await user.add_roles(staff_role)
            print(f"✅ Выдана роль: {staff_role.name}")

        obzvon_role = interaction.guild.get_role(ROLES['Обзвон_FT'])
        if obzvon_role and obzvon_role in user.roles:
            await user.remove_roles(obzvon_role)
            print(f"✅ Снята роль Обзвон FT с {user.display_name}")
        elif obzvon_role:
            print(f"ℹ️ Роль Обзвон FT отсутствует у {user.display_name}, снимать нечего")
        else:
            print("⚠️ Роль Обзвон FT не найдена на сервере!")

        channel = bot.get_channel(CHANNELS['учет_принятых_повышенных'])
        if channel:
            embed = discord.Embed(
                title="📥 Принят новый сотрудник",
                color=discord.Color.green(),
                timestamp=datetime.datetime.now()
            )
            embed.add_field(name="1. Пинг сотрудника", value=user.mention, inline=False)
            embed.add_field(name="2. Ник", value=user.display_name, inline=False)
            embed.add_field(name="3. Принят на должность", value=f"[{position}]", inline=False)
            embed.add_field(name="4. Кто провел обзвон", value=interaction.user.mention, inline=False)
            await channel.send(embed=embed)

        try:
            welcome_channel = bot.get_channel(CHANNELS['приветствие'])
            if welcome_channel:
                welcome_message = (
                    f"{user.mention}, добро пожаловать! 🎉\n"
                    f"Скинь свою почту Зам. Куратору или Куратору."
                )
                await welcome_channel.send(welcome_message)
                print(f"✅ Приветствие отправлено в канал {welcome_channel.name}")
            else:
                print("⚠️ Канал приветствия не найден!")
        except Exception as e:
            print(f"❌ Ошибка при отправке приветствия: {e}")

        await update_staff_message()
        await interaction.followup.send(f"✅ {user.mention} принят на должность {position}")
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для принять истекло")
    except Exception as e:
        print(f"❌ Ошибка в принять: {e}")
        await send_command_error(interaction, f"❌ Ошибка: {e}")


@bot.tree.command(name="удалитьсостав", description="Удалить сотрудника из состава вручную (роли НЕ снимаются)")
@app_commands.describe(nick="Ник сотрудника (без @)")
@has_staff_role_check()
@punishment_channel_only()
async def remove_staff_manual(interaction: discord.Interaction, nick: str):
    try:
        await interaction.response.defer()
        global staff_list
        clean_nick = normalize_nick(nick)
        existing = next(
            (
                m for m in staff_list
                if normalize_nick(m['nick']).lower() == clean_nick.lower()
            ),
            None
        )
        if not existing:
            await interaction.followup.send(f"❌ {nick} не найден в составе.")
            return
        staff_list = [
            m for m in staff_list
            if normalize_nick(m['nick']).lower() != clean_nick.lower()
        ]
        save_staff()
        await update_staff_message()
        await interaction.followup.send(f"✅ {nick} удалён из состава (роли не сняты)")
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для удалитьсостав истекло")
    except Exception as e:
        print(f"❌ Ошибка в удалитьсостав: {e}")
        await send_command_error(interaction, f"❌ Ошибка: {e}")


@bot.tree.command(name="help", description="Показать список команд и информацию о боте")
async def help_command(interaction: discord.Interaction):
    try:
        embed = discord.Embed(
            title="✨ ArtyStaff Bot — Управление персоналом FT",
            description=(
                "Автоматизация состава, наказаний и доступов.\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.now()
        )
        if bot.user and bot.user.avatar:
            embed.set_thumbnail(url=bot.user.avatar.url)

        embed.add_field(
            name="⚠️ Важно",
            value=(
                "**Все команды, кроме `/stat`, работают ТОЛЬКО в канале наказаний.**\n"
                "`/stat` можно использовать в любом канале."
            ),
            inline=False
        )
        embed.add_field(
            name="📊 Статистика",
            value="`/stat @игрок` — баллы, должность, варны, устники и почта",
            inline=False
        )
        embed.add_field(
            name="⚖️ Наказания",
            value=(
                "`/варн @игрок причина [кол-во]` — выдать варн(ы)\n"
                "`/устник @игрок причина [кол-во]` — выдать устник(и)\n"
                "`/снятьварн @игрок` — снять варн за **500** баллов\n"
                "`/снятьустник @игрок` — снять устник за **175** баллов"
            ),
            inline=False
        )
        embed.add_field(
            name="👥 Состав",
            value=(
                "`/принять @игрок` — принять **только на Стажер**\n"
                "`/повысить @игрок должность` — повысить сотрудника\n"
                "`/снять @игрок причина` — снять сотрудника и доступы\n"
                "`/добавитьсостав ник должность` — добавить в состав без ролей\n"
                "`/удалитьсостав ник` — убрать из состава без снятия ролей\n"
                "`/обновитьсостав` — обновить сообщение состава"
            ),
            inline=False
        )
        embed.add_field(
            name="🔑 Доступ и отгулы",
            value=(
                "`/выдатьдоступ email` — доступ к таблице и отчётам\n"
                "`/отгул @игрок дата` — отгул со списанием **100** баллов"
            ),
            inline=False
        )
        embed.add_field(
            name="🎯 Кто чем может пользоваться",
            value=(
                "**Обзванивающий:** `/принять` (Стажер), `/повысить` только "
                "Стажер → Мл. Поддержка\n"
                "**Зам. Куратора+:** все команды управления, наказания, отгулы, доступы\n"
                "**Все КП-роли:** `/stat`"
            ),
            inline=False
        )
        embed.add_field(
            name="💎 Система баллов",
            value=(
                "💬 Снятие устника — **175** баллов\n"
                "⚠️ Снятие варна — **500** баллов\n"
                "📅 Отгул — **100** баллов"
            ),
            inline=False
        )
        embed.add_field(
            name="❓ Помощь",
            value="`/help` — это сообщение",
            inline=False
        )
        embed.set_footer(
            text="ArtyStaff Bot | FT",
            icon_url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None
        )
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        print(f"❌ Ошибка в help: {e}")
        await send_command_error(interaction, f"❌ Ошибка: {e}")


@bot.tree.command(name="добавитьсостав", description="Добавить сотрудника в состав вручную (ник без @)")
@app_commands.describe(nick="Ник сотрудника (без @)", position="Должность")
@app_commands.autocomplete(position=position_autocomplete)
@has_staff_role_check()
@punishment_channel_only()
async def add_staff_manual(interaction: discord.Interaction, nick: str, position: str):
    try:
        await interaction.response.defer()
        if position not in ALL_POSITIONS:
            positions_list = "\n".join(ALL_POSITIONS)
            await interaction.followup.send(
                f"❌ Должность '{position}' не найдена.\nДоступные должности:\n{positions_list}"
            )
            return
        clean_nick = normalize_nick(nick)
        existing = next(
            (
                m for m in staff_list
                if normalize_nick(m['nick']).lower() == clean_nick.lower()
            ),
            None
        )
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
        await send_command_error(interaction, f"❌ Ошибка: {e}")


@bot.tree.command(name="обновитьсостав", description="Обновить сообщение с составом")
@has_staff_role_check()
@punishment_channel_only()
async def refresh_staff(interaction: discord.Interaction):
    try:
        await interaction.response.defer()
        await update_staff_message()
        await interaction.followup.send("✅ Сообщение состава обновлено.")
    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для обновитьсостав истекло")
    except Exception as e:
        print(f"❌ Ошибка в обновитьсостав: {e}")
        await send_command_error(interaction, f"❌ Ошибка: {e}")


@bot.tree.command(name="отгул", description="Оформить отгул сотруднику за 100 баллов")
@app_commands.describe(
    user="Игрок",
    date="Дата отгула (например: 08.09)"
)
@has_staff_role_check()
@punishment_channel_only()
async def take_off(interaction: discord.Interaction, user: discord.Member, date: str):
    try:
        await interaction.response.defer()

        clean_nick = normalize_nick(user.display_name)
        user_data = sheets.find_user_by_nick(clean_nick)

        if not user_data:
            await interaction.followup.send(
                f"❌ Пользователь {user.mention} не найден в таблице."
            )
            return

        row = user_data['row']
        date = date.strip()

        date_col = sheets.find_column_by_date_for_user(row, date)

        if not date_col:
            await interaction.followup.send(
                f"❌ Дата `{date}` не найдена в таблице."
            )
            return

        points_raw = user_data.get('points', '0')
        points_clean = str(points_raw).strip().replace(' ', '').replace(',', '').replace('\u00a0', '')

        try:
            current_points = int(float(points_clean))
        except (ValueError, TypeError):
            current_points = 0

        print(f"🔍 Отгул: points_raw='{points_raw}', current_points={current_points}")

        COST = 100

        if current_points < COST:
            await interaction.followup.send(
                f"❌ Недостаточно баллов для отгула.\n"
                f"Требуется: **{COST}** баллов\n"
                f"В наличии: **{current_points}** баллов"
            )
            return

        old_value = sheets.get_cell_value(row, date_col)

        empty_values = ['', 'н/а', '—', '-', ' ', 'N/A', 'na', 'нет', 'пусто', '0', 'None', 'null']

        is_empty = (
            old_value is None
            or str(old_value).strip() == ''
            or str(old_value).strip().lower() in empty_values
        )

        if not is_empty:
            await interaction.followup.send(
                f"❌ На дату `{date}` уже есть значение: `{old_value}`\n"
                f"Чтобы перезаписать, используй команду с параметром `force: true`"
            )
            return

        source_value = sheets.get_cell_value(80, 2)

        if not source_value or "отгул" not in str(source_value).casefold():
            await interaction.followup.send(
                f"❌ В ячейке B80 нет слова 'ОТГУЛ'.\n"
                f"Сейчас там: `{source_value}`"
            )
            return

        copy_success = sheets.copy_cell_style_with_value(80, 2, row, date_col)

        if not copy_success:
            await interaction.followup.send(
                "❌ Ошибка при копировании отгула в таблицу."
            )
            return

        await asyncio.sleep(0.5)

        new_value = sheets.get_cell_value(row, date_col)

        if not new_value or "отгул" not in str(new_value).casefold():
            await interaction.followup.send(
                f"❌ Отгул не записался в таблицу.\n"
                f"В ячейке сейчас: `{new_value}`"
            )
            return

        new_points = current_points - COST
        sheets.update_user(row, 3, str(new_points))

        await asyncio.sleep(0.5)

        check_data = sheets.get_user_data(row)
        saved_points_raw = check_data.get('points', '0')

        saved_points_clean = str(saved_points_raw).strip().replace(' ', '').replace(',', '').replace('\u00a0', '')

        try:
            saved_points = int(float(saved_points_clean))
        except (ValueError, TypeError):
            saved_points = 0

        if saved_points != new_points:
            sheets.update_user(row, 3, str(new_points))
            await asyncio.sleep(0.5)

            check_data = sheets.get_user_data(row)
            saved_points_raw = check_data.get('points', '0')

            saved_points_clean = str(saved_points_raw).strip().replace(' ', '').replace(',', '').replace('\u00a0', '')

            try:
                saved_points = int(float(saved_points_clean))
            except (ValueError, TypeError):
                saved_points = 0

            if saved_points != new_points:
                await interaction.followup.send(
                    f"⚠️ Отгул установлен, но баллы не обновились!\n"
                    f"Ожидалось: `{new_points}`\n"
                    f"В таблице: `{saved_points}`\n"
                    f"Попробуй обновить таблицу вручную."
                )
                return

        await interaction.followup.send(
            f"✅ Отгул оформлен {user.mention} на `{date}`!\n"
            f"📅 Отгул установлен в таблице.\n"
            f"💰 Списано: **{COST}** баллов.\n"
            f"💰 Остаток: **{new_points}** баллов."
        )

    except discord.errors.NotFound:
        print("⚠️ Взаимодействие для отгул истекло")
    except Exception as e:
        print(f"❌ Ошибка в отгул: {e}")
        await send_command_error(interaction, f"❌ Ошибка при оформлении отгула: `{e}`")


@bot.tree.command(
    name="выдатьдоступ",
    description="Выдать доступ к таблице (Читатель) и форме (Респондент) по email"
)
@app_commands.describe(email="Email пользователя")
@has_staff_role_check()
@punishment_channel_only()
async def grant_access(interaction: discord.Interaction, email: str):
    try:
        await interaction.response.defer(ephemeral=False)

        email = email.strip().lower()

        if not email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            await interaction.followup.send("❌ Укажите корректный email.")
            return

        results = []

        try:
            service = get_google_service()
            if not service:
                results.append("❌ Не удалось подключить Google Drive API.")
            else:
                service.permissions().create(
                    fileId=GOOGLE_SHEETS_ID,
                    body={"type": "user", "role": "reader", "emailAddress": email},
                    sendNotificationEmail=True
                ).execute()
                results.append("✅ Доступ к таблице выдан.")
        except Exception as e:
            error_text = str(e)
            if "already" in error_text.lower():
                results.append("⚠️ Доступ к таблице уже был выдан.")
            elif "File not found" in error_text:
                results.append("❌ Таблица не найдена.")
            else:
                results.append(f"❌ Ошибка таблицы: `{error_text[:150]}`")

        try:
            service = get_google_service()
            form_id = os.getenv("GOOGLE_FORM_ID", "").strip()
            if "/d/" in form_id:
                form_id = form_id.split("/d/")[1].split("/")[0]
            elif "/e/" in form_id:
                form_id = form_id.split("/e/")[1].split("/")[0]

            if not form_id:
                results.append("❌ GOOGLE_FORM_ID не указан в .env")
            elif not service:
                results.append("❌ Не удалось подключить Google Drive API для формы.")
            else:
                service.permissions().create(
                    fileId=form_id,
                    body={"type": "user", "role": "reader", "emailAddress": email},
                    sendNotificationEmail=True
                ).execute()
                results.append("✅ Доступ к отчетам выдан.")
        except Exception as e:
            error_text = str(e)
            if "already" in error_text.lower():
                results.append("⚠️ Доступ к форме уже был выдан.")
            elif "File not found" in error_text:
                results.append("❌ Форма не найдена.")
            else:
                results.append(f"❌ Ошибка формы: `{error_text[:150]}`")

        has_error = any(result.startswith("❌") for result in results)
        embed = discord.Embed(
            title="🔑 Выдача доступа",
            color=discord.Color.red() if has_error else discord.Color.green(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="📧 Email", value=f"`{email}`", inline=False)
        embed.add_field(name="📋 Результат", value="\n".join(results), inline=False)
        embed.set_footer(text=f"Выдал: {interaction.user.display_name}")

        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(f"❌ Ошибка в выдатьдоступ: {e}")
        await send_command_error(interaction, f"❌ Ошибка: {e}")


@bot.tree.command(name="sync", description="Принудительно синхронизировать команды (только для админов)")
@has_staff_role_check()
async def sync_commands(interaction: discord.Interaction):
    try:
        await interaction.response.defer()
        bot.tree.copy_global_to(guild=GUILD)
        synced = await bot.tree.sync(guild=GUILD)
        names = ", ".join(f"/{cmd.name}" for cmd in synced)
        await interaction.followup.send(
            f"✅ Команды синхронизированы на сервере: **{len(synced)}**\n{names}"
        )
    except Exception as e:
        await send_command_error(interaction, f"❌ Ошибка синхронизации: {e}")


async def update_staff_message():
    global staff_message_id, staff_list
    channel = bot.get_channel(staff_channel_id)
    if not channel:
        return
    staff_list.sort(key=lambda x: POSITION_ORDER.get(x['position'], 99))
    if not staff_list:
        content = "📋 СОСТАВ FT\n\nСотрудников пока нет"
    else:
        staff_text = ""
        for member in staff_list:
            staff_text += f"• {member['nick']} — {member['position']}\n"
        content = f"""📋 СОСТАВ FT

```
{staff_text}```

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


def parse_line(line):
    line = line.strip()
    if '. ' in line:
        return line.split('. ', 1)[1]
    elif ') ' in line:
        return line.split(') ', 1)[1]
    return line


def extract_discord_id(text):
    match = re.search(r'<@!?(\d+)>', text)
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
    position = extract_position(line3) or 'Стажер'
    existing = next(
        (m for m in staff_list if normalize_nick(m['nick']).lower() == clean_nick.lower()),
        None
    )
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
    obzvon_role = message.guild.get_role(ROLES['Обзвон_FT'])
    if obzvon_role and obzvon_role in user.roles:
        await user.remove_roles(obzvon_role)
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
        if normalize_nick(member['nick']).lower() == clean_nick.lower():
            old_position = member['position']
            member['position'] = new_position
            save_staff()
            await swap_position_roles(message.guild, user, old_position, new_position)
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
    staff_list = [
        m for m in staff_list
        if normalize_nick(m['nick']).lower() != clean_nick.lower()
    ]
    save_staff()
    for role in user.roles:
        if role.id in ROLES_TO_REMOVE:
            try:
                await user.remove_roles(role)
            except Exception:
                pass
    await update_staff_message()
    print(f"✅ Снят: {clean_nick}")


def run_web():
    try:
        from web import app as web_app
        port = int(os.getenv("PORT", 10000))
        web_app.run(host="0.0.0.0", port=port)
    except Exception as e:
        print(f"❌ Ошибка веб-сервера: {e}")


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot.run(DISCORD_TOKEN)
