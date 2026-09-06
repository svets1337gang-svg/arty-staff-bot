import gspread
from google.oauth2.service_account import Credentials
from config import GOOGLE_SHEETS_ID, SHEET_NAME
import re
from googleapiclient.discovery import build
import os

def normalize_nick(nick: str) -> str:
    """Нормализует ник перед сравнением с ником из Google Sheets."""
    if nick is None:
        return ''

    # Приводим похожие Unicode-символы к единой форме и убираем невидимые
    # символы, которые часто появляются в Discord-никах/копировании.
    nick = nick.replace('\u200b', '').replace('\u200c', '').replace('\u200d', '').replace('\ufeff', '')
    nick = re.sub(r'[\u0000-\u001f\u007f]', '', nick)
    nick = nick.replace('\xa0', ' ')

    nick = re.sub(r'\[[^\]]*\]', '', nick)
    nick = re.sub(r'\([^)]*\)', '', nick)
    nick = re.sub(r'\{[^}]*\}', '', nick)

    return ' '.join(nick.split()).strip()


def nick_key(nick: str) -> str:
    """Ключ для точного, но регистронезависимого сравнения ника."""
    import unicodedata
    return unicodedata.normalize('NFKC', normalize_nick(nick)).casefold()

class SheetsManager:
    def __init__(self):
        self.gc = gspread.service_account(filename='credentials.json')
        self.sheet = self.gc.open_by_key(GOOGLE_SHEETS_ID).worksheet(SHEET_NAME)
    
    def _find_user_in_rows(self, nick, allow_partial=False):
        """Ищет пользователя только по первой колонке таблицы (ник)."""
        target = nick_key(nick)
        if not target:
            return None

        all_values = self.sheet.get_all_values()

        # Сначала только точное совпадение. Это основной путь для команд.
        for row_num, row in enumerate(all_values, start=1):
            if row_num == 1 or not row or not row[0]:
                continue
            if nick_key(row[0]) == target:
                return self.get_user_data(row_num)

        if not allow_partial:
            return None

        # Частичный поиск — только запасной вариант и тоже только по колонке A.
        for row_num, row in enumerate(all_values, start=1):
            if row_num == 1 or not row or not row[0]:
                continue
            table_key = nick_key(row[0])
            if target in table_key or table_key in target:
                return self.get_user_data(row_num)

        return None

    def find_user_by_nick(self, nick):
        try:
            user = self._find_user_in_rows(nick, allow_partial=False)
            if user:
                print(f"✅ Ник найден точно: '{user['nick']}' (строка {user['row']})")
            return user
        except Exception as e:
            print(f"❌ Ошибка поиска ника '{nick}': {e}")
            return None

    def find_user_by_nick_fuzzy(self, nick):
        """Ищет по нику с точным совпадением, затем с безопасным частичным."""
        try:
            clean_nick = normalize_nick(nick)
            print(f"🔍 Ищем ник: '{clean_nick}'")
            user = self._find_user_in_rows(clean_nick, allow_partial=True)
            if user:
                print(f"✅ Найден пользователь '{user['nick']}' в строке {user['row']}")
                return user

            print(f"❌ Пользователь '{nick}' не найден")
            return None
        except Exception as e:
            print(f"❌ Ошибка поиска: {e}")
            return None
    
    def get_user_data(self, row):
        values = self.sheet.row_values(row)
        return {
            'row': row,
            'nick': values[0] if len(values) > 0 else '',
            'position': values[1] if len(values) > 1 else '',
            'points': values[2].strip() if len(values) > 2 else '0',
            'warns': values[3] if len(values) > 3 else '0/3',
            'warns_count': self.parse_count(values[3]) if len(values) > 3 else 0,
            'warns_max': 3,
            'warnings': values[4] if len(values) > 4 else '0/3',
            'warnings_count': self.parse_count(values[4]) if len(values) > 4 else 0,
            'warnings_max': 3,
            'email': values[5] if len(values) > 5 else '',
        }
    
    def parse_count(self, value):
        if value is None:
            return 0
        match = re.search(r'[-+]?\d+', str(value).replace('\xa0', ' '))
        return int(match.group()) if match else 0

    def parse_points(self, value):
        """Безопасно преобразует баллы из значения Google Sheets в целое число."""
        if value is None:
            return 0

        text = str(value).strip().replace('\xa0', '').replace(' ', '')
        if not text:
            return 0

        # Поддерживаем как 1000.5, так и 1000,5; лишний текст игнорируем.
        match = re.search(r'-?\d+(?:[.,]\d+)?', text)
        if not match:
            return 0

        number = match.group().replace(',', '.')
        try:
            return int(float(number))
        except (TypeError, ValueError):
            return 0
    
    def update_user(self, row, column, value):
        self.sheet.update_cell(row, column, value)
    
    def add_user(self, nick, position, points=0, warns='0/3', warnings='0/3', email=''):
        clean_nick = normalize_nick(nick)
        row = [clean_nick, position, str(points), warns, warnings, email]
        self.sheet.append_row(row)
    
    def get_all_users(self):
        values = self.sheet.get_all_values()
        users = []
        
        excluded = ['Вакантно', 'Вакансия', '']
        
        for row_num, row in enumerate(values, start=1):
            if row_num == 1:
                continue
            
            if not row or not row[0]:
                continue
            
            nick = row[0].strip()
            position = row[1].strip() if len(row) > 1 else ''
            
            if position in excluded:
                continue
            
            users.append({
                'row': row_num,
                'nick': nick,
                'position': position,
                'points': row[2] if len(row) > 2 else '0',
                'warns': row[3] if len(row) > 3 else '0/3',
                'warnings': row[4] if len(row) > 4 else '0/3',
                'email': row[5] if len(row) > 5 else '',
            })
        
        return users
    
    # ===== НОВЫЕ МЕТОДЫ =====
    
    def find_user_by_email(self, email):
        try:
            target = (email or '').strip().casefold()
            if not target:
                return None
            all_values = self.sheet.get_all_values()
            for row_num, row in enumerate(all_values, start=1):
                if row_num == 1 or len(row) < 6:
                    continue
                candidate = (row[5] or '').strip().casefold()
                if candidate == target:
                    return self.get_user_data(row_num)
        except Exception as e:
            print(f"❌ Ошибка поиска email '{email}': {e}")
        return None
    
    def remove_penalty(self, nick: str, penalty_type: str) -> dict:
        print(f"\n===== ОТЛАДКА remove_penalty =====")
        print(f"1. Ищем пользователя: {nick}")
        
        user_data = self.find_user_by_nick(nick)
        if not user_data:
            return {'success': False, 'message': f'❌ Пользователь {nick} не найден в таблице.'}
        
        print(f"2. user_data получен:")
        print(f"   - nick: {user_data['nick']}")
        print(f"   - position: {user_data['position']}")
        print(f"   - points: '{user_data['points']}' (тип: {type(user_data['points'])})")
        print(f"   - warns: {user_data['warns']}")
        print(f"   - warnings: {user_data['warnings']}")
        print(f"   - email: {user_data['email']}")
        
        costs = {
            'устник': 50,
            'варн': 100
        }
        
        if penalty_type not in costs:
            return {'success': False, 'message': f'❌ Неизвестный тип взыскания: {penalty_type}. Доступно: устник, варн'}
        
        points_str = str(user_data['points']).strip()
        print(f"3. points_str: '{points_str}'")
        current_points = self.parse_points(points_str)
        print(f"4. Итоговые баллы: {current_points}")
        print(f"====================================\n")
        
        count_field = 'warnings_count' if penalty_type == 'устник' else 'warns_count'
        current_count = user_data[count_field]
        
        if current_count <= 0:
            return {'success': False, 'message': f'❌ У сотрудника нет {penalty_type}ов для снятия.'}
        
        cost = costs[penalty_type]
        
        if current_points < cost:
            return {
                'success': False,
                'message': f'❌ Недостаточно баллов для снятия {penalty_type}. Требуется {cost}, в наличии: {current_points}.'
            }
        
        new_points = current_points - cost
        new_count = current_count - 1
        new_count_str = f'{new_count}/3'
        
        row = user_data['row']
        self.update_user(row, 3, str(new_points))
        col = 4 if penalty_type == 'варн' else 5
        self.update_user(row, col, new_count_str)
        
        return {
            'success': True,
            'message': f'✅ Снят {penalty_type}. Баллы: {current_points} → {new_points}. Осталось: {new_count_str}',
            'new_balance': new_points,
            'new_count': new_count_str
        }
    
    @staticmethod
    def _extract_google_file_id(value: str) -> str:
        """Извлекает именно FILE_ID формы.

        ВАЖНО: ссылка /forms/d/e/... — это responder/publish URL,
        её идентификатор НЕ является Drive fileId и через Drive API
        удалить право доступа нельзя. Для GOOGLE_FORM_ID нужна ссылка
        вида /forms/d/<FILE_ID>/edit либо сам FILE_ID.
        """
        value = (value or '').strip()
        if not value:
            return ''

        # Правильная ссылка редактора Google Forms.
        match = re.search(r'https?://docs\.google\.com/forms/d/([A-Za-z0-9_-]+)/', value)
        if match:
            return match.group(1)

        # Явно не принимаем responder URL вида /forms/d/e/1FAIpQL...
        # как Drive fileId: это другой идентификатор.
        if re.search(r'/forms/d/e/', value):
            return ''

        # Сырая ссылка/ID Drive-файла.
        match = re.search(r'/file/d/([A-Za-z0-9_-]+)', value)
        if match:
            return match.group(1)

        # Если в .env уже указан чистый fileId.
        if re.fullmatch(r'[A-Za-z0-9_-]{20,}', value):
            return value
        return ''

    @staticmethod
    def _same_email(left: str, right: str) -> bool:
        return bool(left and right and left.strip().casefold() == right.strip().casefold())

    def _list_all_permissions(self, drive_service, file_id: str):
        """Получает ВСЕ permissions, включая следующие страницы."""
        result = []
        page_token = None

        while True:
            kwargs = {
                'fileId': file_id,
                'pageSize': 100,
                'fields': 'nextPageToken,permissions(id,emailAddress,type,role,view,domain,displayName,deleted,permissionDetails)',
            }
            if page_token:
                kwargs['pageToken'] = page_token

            response = drive_service.permissions().list(**kwargs).execute()
            result.extend(response.get('permissions', []))
            page_token = response.get('nextPageToken')
            if not page_token:
                break

        return result

    def _delete_matching_permissions(self, drive_service, file_id: str, email: str):
        """Удаляет все прямые permissions пользователя и проверяет результат."""
        target_email = (email or '').strip()
        permissions = self._list_all_permissions(drive_service, file_id)

        matched = []
        blocked = []
        for perm in permissions:
            if perm.get('deleted') or not self._same_email(perm.get('emailAddress', ''), target_email):
                continue
            matched.append(perm)

        for perm in matched:
            try:
                drive_service.permissions().delete(
                    fileId=file_id,
                    permissionId=perm['id'],
                    supportsAllDrives=True,
                ).execute()
            except Exception as delete_error:
                blocked.append((perm, str(delete_error)))

        # Повторно читаем ACL. Без этой проверки нельзя говорить, что доступ снят.
        remaining = self._list_all_permissions(drive_service, file_id)
        still_present = [
            perm for perm in remaining
            if not perm.get('deleted') and self._same_email(perm.get('emailAddress', ''), target_email)
        ]

        return matched, blocked, still_present, remaining

    def remove_form_access(self, email: str) -> dict:
        form_id = ''
        try:
            raw_form_id = os.getenv('GOOGLE_FORM_ID', '')
            form_id = self._extract_google_file_id(raw_form_id)
            if not form_id:
                if '/forms/d/e/' in raw_form_id:
                    return {
                        'success': False,
                        'message': (
                            '❌ В GOOGLE_FORM_ID указана ссылка для заполнения формы (/forms/d/e/...). '
                            'Нужен ID формы из ссылки редактора: https://docs.google.com/forms/d/FILE_ID/edit'
                        )
                    }
                return {'success': False, 'message': '⚠️ GOOGLE_FORM_ID не указан или имеет неверный формат.'}

            target_email = (email or '').strip()
            if not target_email:
                return {'success': False, 'message': '⚠️ Email сотрудника пустой.'}

            creds = Credentials.from_service_account_file('credentials.json')
            drive_service = build('drive', 'v3', credentials=creds, cache_discovery=False)

            matched, blocked, still_present, all_permissions = self._delete_matching_permissions(
                drive_service, form_id, target_email
            )

            if still_present:
                roles = ', '.join(sorted({str(p.get('role') or p.get('view') or 'unknown') for p in still_present}))
                if blocked:
                    reason = blocked[0][1]
                    return {
                        'success': False,
                        'message': (
                            f'❌ Доступ к форме для {target_email} НЕ снят. '
                            f'Найдено разрешений: {len(matched)}, осталось: {len(still_present)}, '
                            f'роли: {roles}. Ошибка удаления: {reason}'
                        )
                    }
                return {
                    'success': False,
                    'message': (
                        f'❌ После удаления доступ к форме для {target_email} всё ещё существует. '
                        f'Осталось разрешений: {len(still_present)} (роли: {roles}).'
                    )
                }

            if matched:
                return {
                    'success': True,
                    'message': f'✅ Доступ к форме для {target_email} удалён. Удалено разрешений: {len(matched)}.'
                }

            # Если личного permission нет, проверяем общий доступ.
            general = [
                p for p in all_permissions
                if not p.get('deleted') and p.get('type') in ('anyone', 'domain', 'group')
            ]
            if general:
                scopes = ', '.join(sorted({str(p.get('type')) for p in general}))
                return {
                    'success': False,
                    'message': (
                        f'⚠️ Личного доступа для {target_email} в ACL формы нет, но форма доступна через общий доступ '
                        f'({scopes}). Такому пользователю нельзя запретить доступ одним удалением его email — '
                        'нужно убрать/изменить общий доступ или группу в Google Forms.'
                    )
                }

            return {
                'success': True,
                'message': f'⚠️ Индивидуальный доступ к форме для {target_email} не найден.'
            }

        except Exception as e:
            error_text = str(e)
            if 'File not found' in error_text or 'notFound' in error_text:
                return {'success': False, 'message': f'❌ Форма с ID {form_id} не найдена. Проверьте GOOGLE_FORM_ID.'}
            if '403' in error_text or 'insufficientPermissions' in error_text:
                return {
                    'success': False,
                    'message': '❌ Нет прав на изменение доступа к форме. Сервисный аккаунт должен иметь права управлять доступом к форме.'
                }
            return {'success': False, 'message': f'❌ Ошибка при удалении доступа к форме: {error_text}'}

    def full_remove_user(self, email: str) -> dict:
        try:
            user_data = self.find_user_by_email(email)
            if not user_data:
                return {'success': False, 'message': f'❌ Email {email} не найден в таблице.'}
            
            nick = user_data['nick']
            row = user_data['row']
            
            creds = Credentials.from_service_account_file('credentials.json')
            drive_service = build('drive', 'v3', credentials=creds)
            
            drive_message = ""
            forms_message = ""
            
            try:
                permissions = drive_service.permissions().list(
                    fileId=GOOGLE_SHEETS_ID,
                    fields='permissions(id, emailAddress)'
                ).execute()
                
                removed_table = 0
                for perm in permissions.get('permissions', []):
                    if self._same_email(perm.get('emailAddress', ''), email):
                        drive_service.permissions().delete(
                            fileId=GOOGLE_SHEETS_ID,
                            permissionId=perm['id']
                        ).execute()
                        removed_table += 1

                if removed_table:
                    drive_message = f"✅ Доступ к таблице для {email} удалён ({removed_table} разреш.)."
                else:
                    drive_message = f"⚠️ Доступ к таблице для {email} не найден."
            except Exception as e:
                drive_message = f"❌ Ошибка при удалении доступа к таблице: {str(e)}"
            
            try:
                result = self.remove_form_access(email)
                forms_message = result['message']
            except Exception as e:
                forms_message = f"❌ Ошибка при удалении доступа к форме: {str(e)}"
            
            self.update_user(row, 6, '')
            
            return {
                'success': True,
                'message': f"Сотрудник {nick} ({email}) уволен.\n{drive_message}\n{forms_message}"
            }
        
        except Exception as e:
            return {'success': False, 'message': f'❌ Критическая ошибка: {str(e)}'}
