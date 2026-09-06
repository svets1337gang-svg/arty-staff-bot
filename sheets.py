import gspread
from google.oauth2.service_account import Credentials
from config import GOOGLE_SHEETS_ID, SHEET_NAME
import re
from googleapiclient.discovery import build
import os

def normalize_nick(nick: str) -> str:
    if not nick:
        return nick
    nick = re.sub(r'\[[^\]]*\]', '', nick)
    nick = re.sub(r'\([^)]*\)', '', nick)
    nick = re.sub(r'\{[^}]*\}', '', nick)
    return nick.strip()

class SheetsManager:
    def __init__(self):
        self.gc = gspread.service_account(filename='credentials.json')
        self.sheet = self.gc.open_by_key(GOOGLE_SHEETS_ID).worksheet(SHEET_NAME)
    
    def find_user_by_nick(self, nick):
        try:
            clean_nick = normalize_nick(nick)
            cell = self.sheet.find(clean_nick)
            if cell:
                row = cell.row
                return self.get_user_data(row)
        except:
            return None
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
        if not value:
            return 0
        parts = value.split('/')
        return int(parts[0]) if parts else 0
    
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
            if not email:
                return None
            cell = self.sheet.find(email)
            if cell:
                return self.get_user_data(cell.row)
        except:
            return None
        return None
    
    def remove_penalty(self, nick: str, penalty_type: str) -> dict:
        user_data = self.find_user_by_nick(nick)
        if not user_data:
            return {'success': False, 'message': f'❌ Пользователь {nick} не найден в таблице.'}
        
        costs = {
            'устник': 50,
            'варн': 100
        }
        
        if penalty_type not in costs:
            return {'success': False, 'message': f'❌ Неизвестный тип взыскания: {penalty_type}. Доступно: устник, варн'}
        
        # ===== ИСПРАВЛЕНО: читаем баллы через float =====
        points_str = str(user_data['points']).strip()
        try:
            current_points = int(float(points_str))
        except (ValueError, TypeError):
            current_points = 0
        
        print(f"🔍 Отладка: ник={nick}, points_str='{points_str}', current_points={current_points}")
        
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
    
    def remove_form_access(self, email: str) -> dict:
        try:
            form_id = os.getenv('GOOGLE_FORM_ID')
            if not form_id:
                return {'success': False, 'message': '⚠️ ID формы не указан в .env'}
            
            if '/e/' in form_id:
                form_id = form_id.split('/e/')[-1].split('/')[0]
            
            creds = Credentials.from_service_account_file('credentials.json')
            drive_service = build('drive', 'v3', credentials=creds)
            
            permissions = drive_service.permissions().list(
                fileId=form_id,
                fields='permissions(id, emailAddress, role)'
            ).execute()
            
            for perm in permissions.get('permissions', []):
                if perm.get('emailAddress') == email:
                    drive_service.permissions().delete(
                        fileId=form_id,
                        permissionId=perm['id']
                    ).execute()
                    return {'success': True, 'message': f'✅ Доступ к форме для {email} удалён.'}
            
            return {'success': True, 'message': f'⚠️ Доступ к форме для {email} не найден.'}
        
        except Exception as e:
            if 'File not found' in str(e):
                return {'success': False, 'message': f'❌ Форма с ID {form_id} не найдена. Проверьте GOOGLE_FORM_ID.'}
            elif '403' in str(e):
                return {'success': False, 'message': f'❌ Нет прав на управление формой. Добавьте сервисный аккаунт как редактора формы.'}
            else:
                return {'success': False, 'message': f'❌ Ошибка при удалении доступа к форме: {str(e)}'}
    
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
                
                permission_id = None
                for perm in permissions.get('permissions', []):
                    if perm.get('emailAddress') == email:
                        permission_id = perm.get('id')
                        break
                
                if permission_id:
                    drive_service.permissions().delete(
                        fileId=GOOGLE_SHEETS_ID,
                        permissionId=permission_id
                    ).execute()
                    drive_message = f"✅ Доступ к таблице для {email} удалён."
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
