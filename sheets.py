import gspread
from google.oauth2.service_account import Credentials
from config import GOOGLE_SHEETS_ID, SHEET_NAME
import re
from googleapiclient.discovery import build
import os


def normalize_nick(nick: str) -> str:
    if not nick:
        return ''

    # Убираем невидимые Unicode-символы
    nick = re.sub(r'[\u200b-\u200f\u202a-\u202e\ufeff]', '', str(nick))

    # Убираем служебные части ника
    nick = re.sub(r'\[[^\]]*\]', '', nick)
    nick = re.sub(r'\([^)]*\)', '', nick)
    nick = re.sub(r'\{[^}]*\}', '', nick)

    return ' '.join(nick.split()).strip().casefold()


def normalize_email(email: str) -> str:
    return str(email or '').strip().casefold()


def parse_points(value) -> int:
    if value is None:
        return 0

    s = str(value).strip().casefold()

    s = (
        s
        .replace('\u00a0', ' ')
        .replace('баллов', '')
        .replace('балла', '')
        .replace('балл', '')
    )

    s = s.replace(' ', '').replace(',', '.')

    match = re.search(r'-?\d+(?:\.\d+)?', s)

    if not match:
        return 0

    try:
        return int(float(match.group(0)))
    except (TypeError, ValueError):
        return 0


class SheetsManager:

    def __init__(self):
        self.gc = gspread.service_account(
            filename='credentials.json'
        )

        self.sheet = self.gc.open_by_key(
            GOOGLE_SHEETS_ID
        ).worksheet(
            SHEET_NAME
        )

    # =========================================================
    # ПОИСК ПО НИКУ
    # =========================================================

    def find_user_by_nick(self, nick):

        try:
            clean_nick = normalize_nick(nick)

            if not clean_nick:
                return None

            # Ищем ТОЛЬКО в колонке A
            values = self.sheet.col_values(1)

            for row_num, value in enumerate(values, start=1):

                if normalize_nick(value) == clean_nick:
                    return self.get_user_data(row_num)

        except Exception as e:
            print(f"Ошибка поиска ника: {e}")

        return None


    def find_user_by_nick_fuzzy(self, nick):

        try:
            clean_nick = normalize_nick(nick)

            if not clean_nick:
                return None

            values = self.sheet.col_values(1)

            # Сначала точное совпадение
            for row_num, value in enumerate(values, start=1):

                if normalize_nick(value) == clean_nick:
                    return self.get_user_data(row_num)

            # Затем частичное совпадение
            for row_num, value in enumerate(values, start=1):

                if row_num == 1:
                    continue

                table_nick = normalize_nick(value)

                if not table_nick:
                    continue

                if (
                    clean_nick in table_nick
                    or
                    table_nick in clean_nick
                ):
                    return self.get_user_data(row_num)

        except Exception as e:
            print(f"Ошибка fuzzy-поиска: {e}")

        return None


    # =========================================================
    # ДАННЫЕ ПОЛЬЗОВАТЕЛЯ
    # =========================================================

    def get_user_data(self, row):

        values = self.sheet.row_values(row)

        return {
            'row': row,

            'nick':
                values[0]
                if len(values) > 0
                else '',

            'position':
                values[1]
                if len(values) > 1
                else '',

            'points':
                values[2].strip()
                if len(values) > 2
                else '0',

            'warns':
                values[3]
                if len(values) > 3
                else '0/3',

            'warns_count':
                self.parse_count(values[3])
                if len(values) > 3
                else 0,

            'warns_max':
                3,

            'warnings':
                values[4]
                if len(values) > 4
                else '0/3',

            'warnings_count':
                self.parse_count(values[4])
                if len(values) > 4
                else 0,

            'warnings_max':
                3,

            'email':
                values[5]
                if len(values) > 5
                else '',
        }


    def parse_count(self, value):

        if not value:
            return 0

        match = re.search(
            r'\d+',
            str(value)
        )

        if not match:
            return 0

        return int(match.group(0))


    # =========================================================
    # ИЗМЕНЕНИЕ ТАБЛИЦЫ
    # =========================================================

def update_user(self, row, column, value):
    try:
        self.sheet.update_cell(row, column, value)

        # Проверяем, что Google Таблицы реально сохранили значение
        saved_value = self.sheet.cell(row, column).value

        if str(saved_value).strip() != str(value).strip():
            print(
                f"❌ Ошибка записи в таблицу: "
                f"ожидалось '{value}', получено '{saved_value}'"
            )
            return False

        print(
            f"✅ Таблица обновлена: "
            f"строка {row}, колонка {column} → '{value}'"
        )
        return True

    except Exception as e:
        print(f"❌ Ошибка update_user: {e}")
        return False


    def add_user(
        self,
        nick,
        position,
        points=0,
        warns='0/3',
        warnings='0/3',
        email=''
    ):

        clean_nick = normalize_nick(nick)

        row = [
            clean_nick,
            position,
            str(points),
            warns,
            warnings,
            email
        ]

        self.sheet.append_row(row)


    def get_all_users(self):

        values = self.sheet.get_all_values()

        users = []

        excluded = [
            'Вакантно',
            'Вакансия',
            ''
        ]

        for row_num, row in enumerate(
            values,
            start=1
        ):

            if row_num == 1:
                continue

            if not row or not row[0]:
                continue

            nick = row[0].strip()

            position = (
                row[1].strip()
                if len(row) > 1
                else ''
            )

            if position in excluded:
                continue

            users.append({
                'row': row_num,

                'nick':
                    nick,

                'position':
                    position,

                'points':
                    row[2]
                    if len(row) > 2
                    else '0',

                'warns':
                    row[3]
                    if len(row) > 3
                    else '0/3',

                'warnings':
                    row[4]
                    if len(row) > 4
                    else '0/3',

                'email':
                    row[5]
                    if len(row) > 5
                    else '',
            })

        return users


    # =========================================================
    # ПОИСК ПО EMAIL
    # =========================================================

    def find_user_by_email(self, email):

        try:

            target = normalize_email(email)

            if not target:
                return None

            # Email находится в колонке F
            values = self.sheet.col_values(6)

            for row_num, value in enumerate(
                values,
                start=1
            ):

                if normalize_email(value) == target:
                    return self.get_user_data(row_num)

        except Exception as e:
            print(
                f"Ошибка поиска email: {e}"
            )

        return None


    # =========================================================
    # СНЯТИЕ ВАРНА / УСТНИКА
    # =========================================================

    def remove_penalty(
        self,
        nick: str,
        penalty_type: str
    ) -> dict:

        print(
            "\n===== ОТЛАДКА remove_penalty ====="
        )

        print(
            f"1. Ищем пользователя: {nick}"
        )

        user_data = self.find_user_by_nick(nick)

        if not user_data:

            return {
                'success': False,
                'message':
                    f'❌ Пользователь {nick} '
                    f'не найден в таблице.'
            }

        print(
            f"2. user_data получен:"
        )

        print(
            f"   - nick: {user_data['nick']}"
        )

        print(
            f"   - position: "
            f"{user_data['position']}"
        )

        print(
            f"   - points: "
            f"'{user_data['points']}'"
        )

        print(
            f"   - warns: "
            f"{user_data['warns']}"
        )

        print(
            f"   - warnings: "
            f"{user_data['warnings']}"
        )

        print(
            f"   - email: "
            f"{user_data['email']}"
        )

        costs = {
            'устник': 175,
            'варн': 500
        }

        if penalty_type not in costs:

            return {
                'success': False,
                'message':
                    '❌ Неизвестный тип взыскания.'
            }

        points_str = str(
            user_data['points']
        ).strip()

        print(
            f"3. points_str: "
            f"'{points_str}'"
        )

        current_points = parse_points(
            points_str
        )

        print(
            f"4. Распознанные баллы: "
            f"{current_points}"
        )

        count_field = (
            'warnings_count'
            if penalty_type == 'устник'
            else 'warns_count'
        )

        current_count = user_data[
            count_field
        ]

        if current_count <= 0:

            return {
                'success': False,
                'message':
                    f'❌ У сотрудника нет '
                    f'{penalty_type}ов для снятия.'
            }

        cost = costs[
            penalty_type
        ]

        if current_points < cost:

            return {
                'success': False,
                'message':
                    f'❌ Недостаточно баллов '
                    f'для снятия {penalty_type}. '
                    f'Требуется {cost}, '
                    f'в наличии: '
                    f'{current_points}.'
            }

        new_points = (
            current_points - cost
        )

        new_count = (
            current_count - 1
        )

        new_count_str = (
            f'{new_count}/3'
        )

        row = user_data['row']

        # Баллы — колонка C
        self.update_user(
            row,
            3,
            str(new_points)
        )

        # Варны — D
        # Устники — E
        col = (
            4
            if penalty_type == 'варн'
            else 5
        )

        self.update_user(
            row,
            col,
            new_count_str
        )

        return {
            'success': True,

            'message':
                f'✅ Снят {penalty_type}. '
                f'Баллы: '
                f'{current_points} → '
                f'{new_points}. '
                f'Осталось: '
                f'{new_count_str}',

            'new_balance':
                new_points,

            'new_count':
                new_count_str
        }


    # =========================================================
    # ОТГУЛ
    # =========================================================

    def find_column_by_date(self, date_str: str) -> int:
        """
        Находит колонку с указанной датой в первой строке.
        date_str: '08.09' - день.месяц
        """
        try:
            if not date_str:
                return None

            # Получаем первую строку (заголовки дат)
            header_row = self.sheet.row_values(1)

            # Ищем дату в заголовках
            for col_idx, value in enumerate(header_row, start=1):
                if value and date_str in str(value):
                    return col_idx

            return None

        except Exception as e:
            print(f"❌ Ошибка поиска даты: {e}")
            return None


    def find_column_by_date_for_user(self, row: int, date_str: str) -> int:
        """
        Находит колонку с указанной датой для конкретного сотрудника.
        Дата ищется в строке 15 (для сотрудников до 60 строки) 
        или в строке 60 (для стажеров).
        """
        try:
            if not date_str:
                return None

            date_str = date_str.strip()

            # Определяем, в каком блоке находится сотрудник
            if row < 60:
                # Блок 1: даты в строке 15
                header_row_num = 15
                print(f"🔍 Сотрудник в блоке 1 (строка {row}), даты в строке {header_row_num}")
            else:
                # Блок 2: даты в строке 60
                header_row_num = 60
                print(f"🔍 Сотрудник в блоке 2 (строка {row}), даты в строке {header_row_num}")

            # Получаем строку с датами
            header_row = self.sheet.row_values(header_row_num)

            print(f"🔍 Ищем дату: '{date_str}'")
            print(f"📋 Заголовки (первые 10): {header_row[:10]}...")

            # Ищем дату в заголовках
            for col_idx, value in enumerate(header_row, start=1):
                if not value:
                    continue

                value_str = str(value).strip()

                # Проверяем разные форматы
                # 1. Точное совпадение
                if date_str == value_str:
                    print(f"✅ Найдено точное совпадение: колонка {col_idx}")
                    return col_idx

                # 2. Вхождение (например, '12.09' в '12.09.2026')
                if date_str in value_str:
                    print(f"✅ Найдено вхождение: колонка {col_idx} ('{value_str}')")
                    return col_idx

                # 3. Без точки
                if date_str.replace('.', '') == value_str.replace('.', '').replace('/', ''):
                    print(f"✅ Найдено совпадение без точки: колонка {col_idx}")
                    return col_idx

            print(f"❌ Дата '{date_str}' не найдена в строке {header_row_num}")
            return None

        except Exception as e:
            print(f"❌ Ошибка поиска даты: {e}")
            return None


    def copy_cell_style_with_value(
        self,
        source_row: int,
        source_col: int,
        target_row: int,
        target_col: int
    ) -> bool:
        """
        Копирует значение и стиль из одной ячейки в другую.
        Используется для копирования ячейки "ОТГУЛ" с оранжевым фоном.
        """
        try:
            spreadsheet = self.gc.open_by_key(GOOGLE_SHEETS_ID)
            sheet_id = self.sheet.id

            body = {
                "requests": [{
                    "copyPaste": {
                        "source": {
                            "sheetId": sheet_id,
                            "startRowIndex": source_row - 1,
                            "endRowIndex": source_row,
                            "startColumnIndex": source_col - 1,
                            "endColumnIndex": source_col
                        },
                        "destination": {
                            "sheetId": sheet_id,
                            "startRowIndex": target_row - 1,
                            "endRowIndex": target_row,
                            "startColumnIndex": target_col - 1,
                            "endColumnIndex": target_col
                        },
                        "pasteType": "PASTE_NORMAL"  # Копирует всё: значение + формат
                    }
                }]
            }

            spreadsheet.batch_update(body)
            return True

        except Exception as e:
            print(f"❌ Ошибка копирования стиля: {e}")
            return False


    def get_cell_value(self, row: int, column: int) -> str:
        """
        Получить значение ячейки по номеру строки и колонки.
        """
        try:
            return self.sheet.cell(row, column).value
        except Exception as e:
            print(f"❌ Ошибка чтения ячейки: {e}")
            return None


    # =========================================================
    # GOOGLE FORM
    # =========================================================

    def _get_form_id(self) -> str:

        form_id = (
            os.getenv(
                'GOOGLE_FORM_ID'
            )
            or ''
        ).strip()

        if not form_id:
            return ''

        # Можно указать целую ссылку:
        # https://docs.google.com/forms/d/ID/edit
        match = re.search(
            r'/forms/d/([^/]+)',
            form_id
        )

        if match:
            return match.group(1)

        # Или просто ID
        return (
            form_id
            .split('?')[0]
            .split('#')[0]
            .strip()
        )


    def _list_all_permissions(
        self,
        drive_service,
        file_id: str,
        include_published: bool = False
    ):

        permissions = []

        page_token = None

        while True:

            kwargs = {
                'fileId':
                    file_id,

                'fields':
                    'nextPageToken,'
                    'permissions('
                    'id,'
                    'emailAddress,'
                    'type,'
                    'role,'
                    'view,'
                    'domain,'
                    'displayName'
                    ')',

                'pageSize':
                    100
            }

            if page_token:
                kwargs[
                    'pageToken'
                ] = page_token

            # КРИТИЧЕСКИ ВАЖНО:
            # это позволяет увидеть
            # "Респондента"
            if include_published:

                kwargs[
                    'includePermissionsForView'
                ] = 'published'

            response = (
                drive_service
                .permissions()
                .list(**kwargs)
                .execute()
            )

            permissions.extend(
                response.get(
                    'permissions',
                    []
                )
            )

            page_token = (
                response.get(
                    'nextPageToken'
                )
            )

            if not page_token:
                break

        return permissions


    def remove_form_access(
        self,
        email: str
    ) -> dict:

        """
        Удаляет прямые права пользователя
        с Google Form:

        - Редактор
        - Респондент

        Для Респондента используется
        published permissions view.
        """

        form_id = self._get_form_id()

        if not form_id:

            return {
                'success': False,
                'message':
                    '⚠️ GOOGLE_FORM_ID '
                    'не указан в .env'
            }

        target_email = normalize_email(
            email
        )

        if not target_email:

            return {
                'success': False,
                'message':
                    '⚠️ Email пустой'
            }

        try:

            creds = (
                Credentials
                .from_service_account_file(
                    'credentials.json'
                )
            )

            drive_service = build(
                'drive',
                'v3',
                credentials=creds
            )

            # ВАЖНО:
            # includePermissionsForView='published'
            # нужен для Респондентов.
            permissions = (
                self._list_all_permissions(
                    drive_service,
                    form_id,
                    include_published=True
                )
            )

            matched = []

            for permission in permissions:

                permission_email = (
                    normalize_email(
                        permission.get(
                            'emailAddress'
                        )
                    )
                )

                if (
                    permission_email
                    != target_email
                ):
                    continue

                if (
                    permission.get('type')
                    == 'user'
                    and permission.get('id')
                ):
                    matched.append(
                        permission
                    )

            if not matched:

                return {
                    'success': False,
                    'message':
                        f'⚠️ Прямой доступ '
                        f'{email} к форме '
                        f'не найден.'
                }

            errors = []

            deleted = []

            for permission in matched:

                permission_id = (
                    permission.get('id')
                )

                try:

                    drive_service \
                        .permissions() \
                        .delete(
                            fileId=form_id,
                            permissionId=
                                permission_id
                        ) \
                        .execute()

                    role = permission.get(
                        'role'
                    )

                    view = permission.get(
                        'view'
                    )

                    if (
                        view == 'published'
                        and role == 'reader'
                    ):
                        deleted.append(
                            'Респондент'
                        )

                    elif role == 'writer':
                        deleted.append(
                            'Редактор'
                        )

                    else:
                        deleted.append(
                            role or
                            'доступ'
                        )

                except Exception as e:

                    errors.append(
                        f"{permission_id}: "
                        f"{e}"
                    )

            # -------------------------------------------------
            # ПРОВЕРКА ПОСЛЕ УДАЛЕНИЯ
            # -------------------------------------------------

            remaining = (
                self._list_all_permissions(
                    drive_service,
                    form_id,
                    include_published=True
                )
            )

            still_present = []

            for permission in remaining:

                if (
                    permission.get('type')
                    != 'user'
                ):
                    continue

                permission_email = (
                    normalize_email(
                        permission.get(
                            'emailAddress'
                        )
                    )
                )

                if (
                    permission_email
                    == target_email
                ):
                    still_present.append(
                        permission
                    )

            # Если остался прямой доступ
            if still_present:

                details = ', '.join(
                    [
                        (
                            f"role="
                            f"{p.get('role')}, "
                            f"view="
                            f"{p.get('view')}"
                        )
                        for p in still_present
                    ]
                )

                return {
                    'success': False,
                    'message':
                        f'❌ Доступ '
                        f'{email} всё ещё '
                        f'есть у формы. '
                        f'{details}'
                }

            if errors:

                return {
                    'success': False,
                    'message':
                        f'❌ Не все права '
                        f'формы удалось '
                        f'удалить для '
                        f'{email}: '
                        f'{"; ".join(errors)}'
                }

            deleted_unique = list(
                dict.fromkeys(
                    deleted
                )
            )

            return {
                'success': True,
                'message':
                    f"✅ Доступ к форме "
                    f"для {email} удалён: "
                    f"{', '.join(deleted_unique)}."
            }

        except Exception as e:

            error_text = str(e)

            if (
                'File not found'
                in error_text
                or '404'
                in error_text
            ):

                return {
                    'success': False,
                    'message':
                        f'❌ Форма с ID '
                        f'{form_id} '
                        f'не найдена. '
                        f'Проверьте '
                        f'GOOGLE_FORM_ID.'
                }

            if (
                '403'
                in error_text
                or
                'insufficient'
                in error_text.lower()
            ):

                return {
                    'success': False,
                    'message':
                        '❌ Нет прав '
                        'управлять доступом '
                        'формы. '
                        'Сервисный аккаунт '
                        'должен иметь доступ '
                        'к форме с правом '
                        'управления доступом.'
                }

            return {
                'success': False,
                'message':
                    f'❌ Ошибка при '
                    f'удалении доступа '
                    f'к форме: '
                    f'{error_text}'
            }


    # =========================================================
    # ПОЛНОЕ УВОЛЬНЕНИЕ
    # =========================================================

    def full_remove_user(
        self,
        email: str
    ) -> dict:

        try:

            user_data = (
                self.find_user_by_email(
                    email
                )
            )

            if not user_data:

                return {
                    'success': False,
                    'message':
                        f'❌ Email {email} '
                        f'не найден в таблице.'
                }

            nick = user_data[
                'nick'
            ]

            row = user_data[
                'row'
            ]

            creds = (
                Credentials
                .from_service_account_file(
                    'credentials.json'
                )
            )

            drive_service = build(
                'drive',
                'v3',
                credentials=creds
            )

            # =================================================
            # УДАЛЕНИЕ ДОСТУПА К ТАБЛИЦЕ
            # =================================================

            try:

                permissions = (
                    self._list_all_permissions(
                        drive_service,
                        GOOGLE_SHEETS_ID,
                        include_published=False
                    )
                )

                target_email = (
                    normalize_email(
                        email
                    )
                )

                matching = [
                    p
                    for p in permissions
                    if (
                        p.get('type')
                        == 'user'
                        and
                        normalize_email(
                            p.get(
                                'emailAddress'
                            )
                        )
                        == target_email
                        and
                        p.get('id')
                    )
                ]

                if matching:

                    errors = []

                    for permission in matching:

                        try:

                            drive_service \
                                .permissions() \
                                .delete(
                                    fileId=
                                        GOOGLE_SHEETS_ID,
                                    permissionId=
                                        permission['id']
                                ) \
                                .execute()

                        except Exception as e:

                            errors.append(
                                str(e)
                            )

                    if errors:

                        drive_message = (
                            '❌ Ошибка при '
                            'удалении доступа '
                            'к таблице: '
                            +
                            '; '.join(
                                errors
                            )
                        )

                    else:

                        drive_message = (
                            f'✅ Доступ к таблице '
                            f'для {email} удалён.'
                        )

                else:

                    drive_message = (
                        f'⚠️ Доступ к таблице '
                        f'для {email} '
                        f'не найден.'
                    )

            except Exception as e:

                drive_message = (
                    '❌ Ошибка при '
                    'удалении доступа '
                    'к таблице: '
                    f'{e}'
                )

            # =================================================
            # УДАЛЕНИЕ ДОСТУПА К ФОРМЕ
            # =================================================

            forms_result = (
                self.remove_form_access(
                    email
                )
            )

            forms_message = (
                forms_result['message']
            )

            # =================================================
            # ОЧИСТКА EMAIL В ТАБЛИЦЕ
            # =================================================

            self.update_user(
                row,
                6,
                ''
            )

            return {
                'success':
                    True,

                'message':
                    f'Сотрудник '
                    f'{nick} ({email}) '
                    f'уволен.\n'
                    f'{drive_message}\n'
                    f'{forms_message}'
            }

        except Exception as e:

            return {
                'success': False,
                'message':
                    f'❌ Критическая '
                    f'ошибка: {e}'
            }
