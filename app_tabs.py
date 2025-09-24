import os
# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request, render_template_string, send_from_directory, session
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, timezone
from fsrs import FSRS, Card, Rating
import os
import hashlib
import glob

app = Flask(__name__)
CORS(app)

# Конфигурация
postgresql://postgres.qtdaoicutiuodbkxxffz:2o7xzdSYggPoZant@aws-1-eu-north-1.pooler.supabase.com:6543/postgres
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
print(f"DATABASE_URL from environment: {os.environ.get('DATABASE_URL', 'NOT FOUND')}")
print(f"Using database: {app.config['SQLALCHEMY_DATABASE_URI'][:50]}...")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'spanish-anki-secret-key-2025'

db = SQLAlchemy(app)

# Инициализация FSRS
fsrs = FSRS()

# Настройки по времени занятий
TIME_SETTINGS = {
    'light': {
        'name': 'Легкая',
        'minutes': 10,
        'daily_new': 5,
        'daily_review': 30,
        'icon': '🌱',
        'description': '10 минут в день'
    },
    'normal': {
        'name': 'Обычная', 
        'minutes': 20,
        'daily_new': 10,
        'daily_review': 60,
        'icon': '📚',
        'description': '20 минут в день'
    },
    'intensive': {
        'name': 'Интенсивная',
        'minutes': 30,
        'daily_new': 15,
        'daily_review': 100,
        'icon': '🚀',
        'description': '30 минут в день'
    }
}

# Модель карточки
class CardModel(db.Model):
    __tablename__ = 'cards'
    
    id = db.Column(db.Integer, primary_key=True)
    chunk = db.Column(db.String(200), nullable=False)
    trigger = db.Column(db.Text, nullable=False)
    translation = db.Column(db.String(200), nullable=False)
    dialogue = db.Column(db.Text, nullable=False)
    audio_path = db.Column(db.String(200))
    image_path = db.Column(db.String(200))
    level = db.Column(db.String(20))
    front_audio = db.Column(db.String(200), default='')  # ДОБАВИТЬ ЭТУ СТРОКУ
    
    # FSRS параметры
    due = db.Column(db.DateTime, default=datetime.now)
    stability = db.Column(db.Float, default=0)
    difficulty = db.Column(db.Float, default=0)
    elapsed_days = db.Column(db.Integer, default=0)
    scheduled_days = db.Column(db.Integer, default=0)
    reps = db.Column(db.Integer, default=0)
    lapses = db.Column(db.Integer, default=0)
    state = db.Column(db.Integer, default=0)  # 0=New, 1=Learning, 2=Review, 3=Relearning
    last_review = db.Column(db.DateTime)

    def to_dict(self):
    """Конвертация в словарь для JSON"""
    return {
        'id': self.id,
        'chunk': self.chunk,
        'trigger': self.trigger,
        'translation': self.translation,
        'dialogue': self.dialogue,
        'audio_path': self.audio_path,
        'level': self.level,
        'front_audio': self.front_audio,  
        'state': self.state,
        'reps': self.reps
    }

# Модель колоды
class DeckModel(db.Model):
    __tablename__ = 'decks'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    load_type = db.Column(db.String(20), default='normal')
    daily_new = db.Column(db.Integer, default=10)
    daily_review = db.Column(db.Integer, default=60)
    created_at = db.Column(db.DateTime, default=datetime.now)
    last_studied = db.Column(db.DateTime)
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'load_type': self.load_type,
            'daily_new': self.daily_new,
            'daily_review': self.daily_review
        }

# Модель связи колода-карточка
class DeckCardModel(db.Model):
    __tablename__ = 'deck_cards'
    
    deck_id = db.Column(db.Integer, db.ForeignKey('decks.id'), primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey('cards.id'), primary_key=True)
    position = db.Column(db.Integer, default=0)
    added_at = db.Column(db.DateTime, default=datetime.now)

# Модель для отслеживания файлов
class FileTrackingModel(db.Model):
    __tablename__ = 'file_tracking'
    
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), unique=True, nullable=False)
    file_hash = db.Column(db.String(64), nullable=False)
    last_imported = db.Column(db.DateTime, default=datetime.now)
    cards_count = db.Column(db.Integer, default=0)

# Функции для синхронизации
def get_file_hash(filepath):
    """Получить хеш файла для отслеживания изменений"""
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def parse_card_line(line):
    """Парсинг строки с карточкой"""
    parts = line.strip().split('|')
    if len(parts) < 7:
        return None
        
    result = {
        'chunk': parts[0],
        'trigger': parts[1], 
        'translation': parts[2],
        'dialogue': parts[3],
        'audio_path': parts[4].replace('[sound:', '').replace(']', '') if parts[4] != '[sound:]' else '',
        'image_path': parts[5].replace('[img:', '').replace(']', ''),
        'level': parts[6]
    }
    
    # Добавляем поле 8 для звука на лицевой стороне
    if len(parts) > 7:
        result['front_audio'] = parts[7].replace('[sound:', '').replace(']', '') if parts[7] else ''
    else:
        result['front_audio'] = ''
    
    return result

def sync_cards_from_directory(directory_path='data', deck_id=None):
    """Синхронизация карточек из директории"""
    import_stats = {
        'new_files': 0,
        'updated_files': 0,
        'new_cards': 0,
        'updated_cards': 0,
        'errors': []
    }
    
    # Получаем все txt файлы
    txt_files = glob.glob(os.path.join(directory_path, '*.txt'))
    
    for filepath in txt_files:
        filename = os.path.basename(filepath)
        
        try:
            # Получаем хеш файла
            current_hash = get_file_hash(filepath)
            
            # Проверяем, импортировали ли мы этот файл раньше
            file_record = FileTrackingModel.query.filter_by(filename=filename).first()
            
            # Если файл не изменился, пропускаем
            if file_record and file_record.file_hash == current_hash:
                continue
                
            # Файл новый или изменился
            if file_record:
                import_stats['updated_files'] += 1
            else:
                import_stats['new_files'] += 1
                file_record = FileTrackingModel(filename=filename)
                db.session.add(file_record)
            
            # Обновляем хеш
            file_record.file_hash = current_hash
            file_record.last_imported = datetime.now(timezone.utc)
            
            # Читаем и обрабатываем карточки
            cards_in_file = 0
            with open(filepath, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    if not line.strip():
                        continue
                        
                    card_data = parse_card_line(line)
                    if not card_data:
                        import_stats['errors'].append(f"{filename}:{line_num} - неверный формат")
                        continue
                    
                    # Ищем существующую карточку по фразе
                    existing_card = CardModel.query.filter_by(chunk=card_data['chunk']).first()
                    
                    if existing_card:
                        # Обновляем существующую карточку
                        changed = False
                        for field in ['trigger', 'translation', 'dialogue', 'level', 'audio_path', 'image_path']:
                            if getattr(existing_card, field) != card_data[field]:
                                setattr(existing_card, field, card_data[field])
                                changed = True
                        
                        if changed:
                            import_stats['updated_cards'] += 1
                    else:
                        # Создаем новую карточку
                        new_card = CardModel(
                            chunk=card_data['chunk'],
                            trigger=card_data['trigger'],
                            translation=card_data['translation'],
                            dialogue=card_data['dialogue'],
                            audio_path=card_data['audio_path'],
                            image_path=card_data['image_path'],
                            level=card_data['level'],
                            front_audio=card_data.get('front_audio', ''),  # <-- ДОБАВИТЬ ЭТУ СТРОКУ
                            state=0,
                            due=datetime.now(timezone.utc)
                        )
                        db.session.add(new_card)
                        db.session.flush()
                        
                        # Добавляем в колоду если указана
                        if deck_id:
                            deck_card = DeckCardModel(
                                deck_id=deck_id,
                                card_id=new_card.id
                            )
                            db.session.add(deck_card)
                        
                        import_stats['new_cards'] += 1
                    
                    cards_in_file += 1
            
            file_record.cards_count = cards_in_file
            
        except Exception as e:
            import_stats['errors'].append(f"{filename}: {str(e)}")
    
    db.session.commit()
    return import_stats

def initial_sync():
    """Начальная синхронизация при запуске приложения"""
    with app.app_context():
        data_dir = os.path.join(os.path.dirname(__file__), 'data')
        if os.path.exists(data_dir):
            print("Запуск начальной синхронизации...")
            stats = sync_cards_from_directory(data_dir, deck_id=1)
            print(f"✅ Начальная синхронизация завершена:")
            print(f"   Новых файлов: {stats['new_files']}")
            print(f"   Обновлено файлов: {stats['updated_files']}")
            print(f"   Новых карточек: {stats['new_cards']}")
            print(f"   Обновлено карточек: {stats['updated_cards']}")
            if stats['errors']:
                print(f"   ⚠️ Ошибки: {stats['errors']}")

# HTML шаблон с вкладками
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Spanish Chunks - FSRS</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            background: #f5f5f5;
            font-family: 'Segoe UI', system-ui, sans-serif;
            padding: 10px;
            min-height: 100vh;
        }
        
        .phone-container {
            width: 100%;
            max-width: 390px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            overflow: hidden;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
            min-height: 600px;
        }
        
        /* Навигация по вкладкам */
        .tabs-nav {
            display: flex;
            background: #fff;
            border-bottom: 2px solid #e0e0e0;
        }
        
        .tab-btn {
            flex: 1;
            padding: 15px 10px;
            border: none;
            background: transparent;
            cursor: pointer;
            font-size: 13px;
            color: #666;
            transition: all 0.3s;
        }
        
        .tab-btn.active {
            color: #007bff;
            font-weight: bold;
            border-bottom: 3px solid #007bff;
            margin-bottom: -2px;
            background: #f0f8ff;
        }
        
        .tab-content {
            display: none;
            max-height: calc(100vh - 100px);
            overflow-y: auto;
        }
        
        .tab-content.active {
            display: block;
        }
        
        /* Стили для вкладки изучения */
        .stats-bar {
            background: #e8eaf6;
            padding: 12px;
            display: flex;
            justify-content: space-around;
            color: #5e6472;
        }
        
        .stat-item {
            text-align: center;
        }
        
        .stat-number {
            font-size: 22px;
            font-weight: bold;
            color: #7986cb;
        }
        
        .stat-label {
            font-size: 10px;
            opacity: 0.9;
            text-transform: uppercase;
        }
        
        .progress-bar {
            background: #fff3cd;
            padding: 10px;
            text-align: center;
        }
        
        .progress-text {
            font-size: 13px;
            color: #856404;
            margin-bottom: 8px;
        }
        
        .progress-visual {
            background: #e0e0e0;
            height: 6px;
            border-radius: 3px;
            overflow: hidden;
        }
        
        .progress-fill {
            background: #4caf50;
            height: 100%;
            transition: width 0.3s;
        }
        
        .level-badge {
            background: #fafafa;
            padding: 8px;
            text-align: center;
            font-size: 12px;
            font-weight: 600;
            color: #9e9e9e;
            letter-spacing: 1px;
        }
        
        .card-content {
            padding: 20px;
        }
        
        .situation-card {
            background: #e3f2fd;
            border-radius: 15px;
            padding: 18px;
            margin: 20px auto;
            max-width: 340px;
            text-align: center;
        }
        
        .situation-label {
            font-size: 11px;
            text-transform: uppercase;
            color: #90a4ae;
            font-weight: bold;
            margin-bottom: 8px;
            letter-spacing: 1px;
        }
        
        .situation-text {
            font-size: 15px;
            color: #546e7a;
            line-height: 1.5;
        }
        
        .dialogue-container {
            background: #fafafa;
            border-radius: 12px;
            padding: 20px;
            margin: 25px auto;
            max-width: 340px;
        }
        
        .dialogue-line {
            background: white;
            padding: 12px 15px;
            margin: 10px 0;
            border-radius: 8px;
            font-size: 17px;
            color: #424242;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        }
        
        .gap-placeholder {
            display: inline-block;
            min-width: 100px;
            height: 3px;
            background: linear-gradient(90deg, #90caf9, #81c784);
            vertical-align: middle;
            margin: 0 8px;
            animation: pulse 2s infinite;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.6; }
        }
        
        .show-answer-btn {
            background: #90caf9;
            color: white;
            border: none;
            padding: 15px 40px;
            border-radius: 30px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            display: block;
            margin: 30px auto;
            box-shadow: 0 4px 12px rgba(144, 202, 249, 0.3);
            transition: transform 0.2s;
        }
        
        .show-answer-btn:hover {
            transform: translateY(-2px);
            background: #64b5f6;
        }
        
        .main-phrase {
            font-size: 36px;
            font-weight: bold;
            color: #424242;
            margin: 20px 0;
            text-align: center;
        }
        
        .translation {
            font-size: 18px;
            color: #7986cb;
            font-style: italic;
            margin: 15px 0;
            text-align: center;
        }
        
        .audio-button {
            background: #90caf9;
            border-radius: 50%;
            width: 60px;
            height: 60px;
            margin: 20px auto;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 28px;
            color: white;
            border: none;
            box-shadow: 0 3px 8px rgba(144, 202, 249, 0.4);
        }
        
        .answer-buttons {
            display: flex;
            gap: 8px;
            justify-content: center;
            flex-wrap: wrap;
            margin-top: 25px;
        }
        
        .rate-btn {
            padding: 10px 18px;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            cursor: pointer;
            color: white;
            transition: all 0.2s;
        }
        
        .btn-again { background: #ef9a9a; }
        .btn-hard { background: #ffcc80; }
        .btn-good { background: #a5d6a7; }
        .btn-easy { background: #80cbc4; }
        
        .rate-btn:hover {
            transform: translateY(-1px);
            opacity: 0.9;
        }
        
        .rate-btn small {
            display: block;
            font-size: 11px;
            margin-top: 2px;
            opacity: 0.9;
        }
        
        /* Стили для вкладки колод */
        .decks-container {
            padding: 20px;
        }
        
        .deck-item {
            background: #f5f5f5;
            border-radius: 10px;
            padding: 15px;
            margin: 10px 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .deck-info h4 {
            margin: 0 0 5px 0;
            color: #333;
        }
        
        .deck-info small {
            color: #666;
        }
        
        .deck-load-selector {
            display: flex;
            gap: 5px;
        }
        
        .deck-load-btn {
            padding: 5px 10px;
            border: 1px solid #ddd;
            background: white;
            border-radius: 5px;
            cursor: pointer;
            font-size: 12px;
        }
        
        .deck-load-btn.active {
            background: #007bff;
            color: white;
            border-color: #007bff;
        }
        
        /* Стили для вкладки редактора */
        .editor-container {
            padding: 20px;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 5px;
            color: #666;
            font-size: 14px;
        }
        
        .form-group input,
        .form-group textarea,
        .form-group select {
            width: 100%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 8px;
            font-size: 14px;
        }
        
        .form-group textarea {
            min-height: 80px;
            resize: vertical;
        }
        
        .btn-submit {
            background: #4caf50;
            color: white;
            border: none;
            padding: 12px 30px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 16px;
            width: 100%;
        }
        
        .btn-submit:hover {
            background: #45a049;
        }
        
        /* Стили для менеджера карточек */
        .manager-container {
            padding: 15px;
        }
        
        .search-box {
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
        }
        
        .search-box input {
            flex: 1;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 14px;
        }
        
        .search-box button {
            padding: 10px 15px;
            background: #007bff;
            color: white;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
        }
        
        .cards-list {
            max-height: 200px;
            overflow-y: auto;
            border: 1px solid #e0e0e0;
            border-radius: 5px;
            margin-bottom: 15px;
        }
        
        .card-item {
            padding: 12px;
            border-bottom: 1px solid #e0e0e0;
            cursor: pointer;
            transition: background 0.2s;
        }
        
        .card-item:hover {
            background: #f8f9fa;
        }
        
        .card-item.selected {
            background: #e3f2fd;
        }
        
        .card-item-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 3px;
        }
        
        .card-chunk {
            font-weight: bold;
            color: #333;
            font-size: 14px;
        }
        
        .card-level {
            background: #6c757d;
            color: white;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 10px;
        }
        
        .card-translation {
            color: #666;
            font-size: 12px;
        }
        
        .button-group {
            display: flex;
            gap: 8px;
            margin-top: 15px;
            flex-wrap: wrap;
        }
        
        .btn {
            padding: 8px 15px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 13px;
            transition: opacity 0.2s;
        }
        
        .btn:hover {
            opacity: 0.8;
        }
        
        .btn-primary { background: #007bff; color: white; }
        .btn-success { background: #28a745; color: white; }
        .btn-danger { background: #dc3545; color: white; }
        .btn-warning { background: #ffc107; color: #333; }
        .btn-secondary { background: #6c757d; color: white; }
        
        .bulk-import {
            margin-top: 20px;
            padding-top: 15px;
            border-top: 2px solid #e0e0e0;
        }
        
        .bulk-import h4 {
            margin-bottom: 10px;
            color: #495057;
            font-size: 16px;
        }
        
        .import-textarea {
            width: 100%;
            min-height: 120px;
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-family: monospace;
            font-size: 11px;
        }
        
        .import-help {
            background: #fff3cd;
            padding: 8px;
            border-radius: 5px;
            margin-bottom: 10px;
            font-size: 11px;
            color: #856404;
        }
        
        .message {
            padding: 10px;
            border-radius: 5px;
            margin-bottom: 10px;
            font-size: 13px;
            display: none;
        }
        
        .message.success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        
        .message.error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
    </style>
</head>
<body>
    <div class="phone-container">
        <!-- Навигация по вкладкам -->
        <div class="tabs-nav">
            <button class="tab-btn active" onclick="switchTab('study')">
                📚<br>Изучение
            </button>
            <button class="tab-btn" onclick="switchTab('decks')">
                📁<br>Колоды
            </button>
            <button class="tab-btn" onclick="switchTab('editor')">
                ➕<br>Добавить
            </button>
            <button class="tab-btn" onclick="switchTab('manager')">
                ⚙️<br>Менеджер
            </button>
        </div>
        
        <!-- Вкладка изучения -->
        <div id="studyTab" class="tab-content active">
            <div style="background: #fff; padding: 10px; border-bottom: 1px solid #e0e0e0;">
                <select id="deckSelector" onchange="changeDeck()" style="width: 100%; padding: 8px; border: 1px solid #e0e0e0; border-radius: 8px; font-size: 16px;">
                    <option value="">Загрузка колод...</option>
                </select>
            </div>
            
            <div class="stats-bar">
                <div class="stat-item">
                    <div class="stat-number" id="newCount">0</div>
                    <div class="stat-label">Новые</div>
                </div>
                <div class="stat-item">
                    <div class="stat-number" id="learningCount">0</div>
                    <div class="stat-label">Изучаются</div>
                </div>
                <div class="stat-item">
                    <div class="stat-number" id="reviewCount">0</div>
                    <div class="stat-label">Повторение</div>
                </div>
            </div>
            
            <div class="progress-bar">
                <div class="progress-text" id="progressText">
                    Сегодня: 0/10 новых, 0/60 повторений
                </div>
                <div class="progress-visual">
                    <div class="progress-fill" id="progressFill" style="width: 0%"></div>
                </div>
            </div>
            
            <div class="level-badge" id="cardLevel">УРОВЕНЬ A2</div>
            
            <div class="card-content" id="cardContent">
                <div style="text-align: center; padding: 50px;">
                    <p>Загрузка...</p>
                </div>
            </div>
        </div>
        
        <!-- Вкладка колод -->
        <div id="decksTab" class="tab-content">
            <div class="decks-container">
                <h3 style="margin-bottom: 20px;">Управление колодами</h3>
                
                <button onclick="createNewDeck()" style="background: #4caf50; color: white; border: none; padding: 10px 20px; border-radius: 8px; cursor: pointer; width: 100%; margin-bottom: 20px;">
                    ➕ Создать новую колоду
                </button>
                
                <div id="decksList">
                    <!-- Список колод загрузится здесь -->
                </div>
            </div>
        </div>
        
        <!-- Вкладка редактора -->
        <div id="editorTab" class="tab-content">
            <div class="editor-container">
                <h3 style="margin-bottom: 20px;">Добавить карточку</h3>
                
                <div class="form-group">
                    <label>Колода:</label>
                    <select id="editorDeckSelect">
                        <option value="">Выберите колоду...</option>
                    </select>
                </div>
                
                <div class="form-group">
                    <label>Фраза (испанский):</label>
                    <input type="text" id="cardChunk" placeholder="Está muy rico">
                </div>
                
                <div class="form-group">
                    <label>Контекст/Триггер:</label>
                    <textarea id="cardTrigger" placeholder="[Primera vez comiendo paella...] —¿Te gusta? —______"></textarea>
                </div>
                
                <div class="form-group">
                    <label>Перевод:</label>
                    <input type="text" id="cardTranslation" placeholder="Очень вкусно">
                </div>
                
                <div class="form-group">
                    <label>Диалог (полный):</label>
                    <textarea id="cardDialogue" placeholder="—¿Te gusta la paella? —¡Está muy rico!"></textarea>
                </div>
                
                <div class="form-group">
                    <label>Уровень:</label>
                    <select id="cardLevel">
                        <option value="A2">A2</option>
                        <option value="B1">B1</option>
                        <option value="B2">B2</option>
                    </select>
                </div>
                
                <button class="btn-submit" onclick="addCard()">
                    Добавить карточку
                </button>
            </div>
        </div>
        
        <!-- Вкладка менеджера карточек -->
        <div id="managerTab" class="tab-content">
            <div class="manager-container">
                <h3 style="margin-bottom: 15px;">🎯 Менеджер карточек</h3>
                
                <div class="message" id="managerMessage"></div>
                
                <!-- БЛОК СИНХРОНИЗАЦИИ -->
                <div style="background: #e8f5e9; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                    <h4 style="margin: 0 0 10px 0; color: #2e7d32;">📂 Синхронизация с папкой data</h4>
                    <p style="font-size: 13px; color: #666; margin: 5px 0;">
                        Путь: backend\\data\\
                    </p>
                    <div id="syncStatus" style="font-size: 12px; color: #666; margin: 10px 0;">📊 Проверка статуса...</div>
                    <div style="display: flex; gap: 10px; margin-top: 10px;">
                        <button onclick="syncCards()" class="btn btn-primary" style="flex: 1;">
                            🔄 Синхронизировать сейчас
                        </button>
                        <button onclick="toggleAutoSync()" id="autoSyncBtn" class="btn btn-secondary" style="flex: 1;">
                            ⚡ Авто-синхронизация: ВЫКЛ
                        </button>
                    </div>
                    <div id="syncResult" style="margin-top: 10px; font-size: 13px;"></div>
                </div>
                
                <div class="search-box">
                    <input type="text" id="searchInput" placeholder="Поиск фразы...">
                    <button onclick="searchCards()">🔍</button>
                </div>
                
                <div class="cards-list" id="cardsList">
                    <div style="padding: 20px; text-align: center; color: #999; font-size: 13px;">
                        Введите фразу для поиска
                    </div>
                </div>
                
                <div style="background: #f8f9fa; padding: 15px; border-radius: 5px;">
                    <input type="hidden" id="editCardId">
                    
                    <div class="form-group">
                        <label>Колода:</label>
                        <select id="managerDeckSelect">
                            <option value="">Выберите колоду...</option>
                        </select>
                    </div>
                    
                    <div class="form-group">
                        <label>Фраза:</label>
                        <input type="text" id="editChunk">
                    </div>
                    
                    <div class="form-group">
                        <label>Контекст:</label>
                        <textarea id="editTrigger" style="min-height: 60px;"></textarea>
                    </div>
                    
                    <div class="form-group">
                        <label>Перевод:</label>
                        <input type="text" id="editTranslation">
                    </div>
                    
                    <div class="form-group">
                        <label>Диалог:</label>
                        <textarea id="editDialogue" style="min-height: 60px;"></textarea>
                    </div>
                    
                    <div class="form-group">
                        <label>Уровень:</label>
                        <select id="editLevel">
                            <option value="A2">A2</option>
                            <option value="A2-B1">A2-B1</option>
                            <option value="B1">B1</option>
                            <option value="B1-B2">B1-B2</option>
                            <option value="B2">B2</option>
                        </select>
                    </div>
                    
                    <div class="button-group">
                        <button class="btn btn-success" onclick="updateCard()">💾 Сохранить</button>
                        <button class="btn btn-warning" onclick="resetCard()">🔄 Сброс</button>
                        <button class="btn btn-danger" onclick="deleteCard()">🗑️ Удалить</button>
                    </div>
                </div>
                
                <div class="bulk-import">
                    <h4>📦 Массовый импорт</h4>
                    <div class="import-help">
                        Формат: Фраза|Контекст|Перевод|Пример|Аудио|Изображение|Уровень
                    </div>
                    
                    <div class="form-group">
                        <select id="importDeckSelect">
                            <option value="">Выберите колоду...</option>
                        </select>
                    </div>
                    
                    <textarea class="import-textarea" id="bulkImportText" placeholder="Вставьте карточки..."></textarea>
                    
                    <button class="btn btn-primary" onclick="bulkImport()" style="width: 100%; margin-top: 10px;">
                        📥 Импортировать
                    </button>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // Глобальные переменные
        let currentCard = null;
        let showingAnswer = false;
        let userLoad = localStorage.getItem('userLoad') || 'normal';
        let todayStats = {new: 0, review: 0, total: 0};
        let currentDeckId = localStorage.getItem('currentDeckId') || 1;
        let allDecks = [];
        let currentEditCardId = null;
        let autoSyncEnabled = false;
        let autoSyncInterval = null;
        
        const loadSettings = {
            'light': {daily_new: 5, daily_review: 30, minutes: 10},
            'normal': {daily_new: 10, daily_review: 60, minutes: 20},
            'intensive': {daily_new: 15, daily_review: 100, minutes: 30}
        };
        
        const TIME_SETTINGS = {
            'light': {
                name: 'Легкая',
                daily_new: 5,
                daily_review: 30,
                minutes: 10
            },
            'normal': {
                name: 'Обычная',
                daily_new: 10,
                daily_review: 60,
                minutes: 20
            },
            'intensive': {
                name: 'Интенсивная',
                daily_new: 15,
                daily_review: 100,
                minutes: 30
            }
        };
        
        // Функция переключения вкладок
        function switchTab(tabName) {
            // Скрываем все вкладки
            document.querySelectorAll('.tab-content').forEach(tab => {
                tab.classList.remove('active');
            });
            
            // Показываем выбранную
            document.getElementById(tabName + 'Tab').classList.add('active');
            
            // Обновляем кнопки
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.classList.remove('active');
            });
            event.target.classList.add('active');
            
            // Загружаем данные для вкладки
            if (tabName === 'decks') {
                loadDecksList();
            } else if (tabName === 'editor') {
                loadDecksForEditor();
            } else if (tabName === 'manager') {
                loadDecksForManager();
                setTimeout(updateSyncStatus, 100);
            }
        }
        
        // Загрузка списка колод
        async function loadDecks() {
            try {
                const response = await fetch('/api/decks');
                const decks = await response.json();
                allDecks = decks;
                
                const selector = document.getElementById('deckSelector');
                selector.innerHTML = '';
                
                decks.forEach(deck => {
                    const option = document.createElement('option');
                    option.value = deck.id;
                    option.textContent = `${deck.name} (${deck.total_cards} карточек)`;
                    if (deck.id == currentDeckId) option.selected = true;
                    selector.appendChild(option);
                });
            } catch (error) {
                console.error('Ошибка загрузки колод:', error);
            }
        }
        
        // Загрузка списка колод для вкладки управления
        async function loadDecksList() {
            try {
                const response = await fetch('/api/decks');
                const decks = await response.json();
                
                const container = document.getElementById('decksList');
                container.innerHTML = '';
                
                decks.forEach(deck => {
                    const deckItem = document.createElement('div');
                    deckItem.className = 'deck-item';
                    deckItem.innerHTML = `
                        <div class="deck-info">
                            <h4>${deck.name}</h4>
                            <small>${deck.total_cards} карточек • ${deck.load_type}</small>
                        </div>
                        <div class="deck-load-selector">
                            <button class="deck-load-btn ${deck.load_type === 'light' ? 'active' : ''}" 
                                    onclick="setDeckLoad(${deck.id}, 'light')">🌱</button>
                            <button class="deck-load-btn ${deck.load_type === 'normal' ? 'active' : ''}" 
                                    onclick="setDeckLoad(${deck.id}, 'normal')">📚</button>
                            <button class="deck-load-btn ${deck.load_type === 'intensive' ? 'active' : ''}" 
                                    onclick="setDeckLoad(${deck.id}, 'intensive')">🚀</button>
                        </div>
                    `;
                    container.appendChild(deckItem);
                });
            } catch (error) {
                console.error('Ошибка загрузки списка колод:', error);
            }
        }
        
        // Загрузка колод для редактора
        async function loadDecksForEditor() {
            const selector = document.getElementById('editorDeckSelect');
            selector.innerHTML = '<option value="">Выберите колоду...</option>';
            
            allDecks.forEach(deck => {
                const option = document.createElement('option');
                option.value = deck.id;
                option.textContent = deck.name;
                selector.appendChild(option);
            });
        }
        
        // Загрузка колод для менеджера
        async function loadDecksForManager() {
            const selectors = ['managerDeckSelect', 'importDeckSelect'];
            selectors.forEach(id => {
                const selector = document.getElementById(id);
                selector.innerHTML = '<option value="">Выберите колоду...</option>';
                
                allDecks.forEach(deck => {
                    const option = document.createElement('option');
                    option.value = deck.id;
                    option.textContent = deck.name;
                    selector.appendChild(option);
                });
            });
        }
        
        // Создание новой колоды
        async function createNewDeck() {
            const name = prompt('Название новой колоды:');
            if (!name) return;
            
            try {
                const response = await fetch('/api/create_deck', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({name: name})
                });
                
                const data = await response.json();
                if (data.success) {
                    alert('Колода создана!');
                    loadDecksList();
                    loadDecks();
                }
            } catch (error) {
                console.error('Ошибка создания колоды:', error);
            }
        }
        
        // Установка нагрузки для колоды
        async function setDeckLoad(deckId, loadType) {
            try {
                const response = await fetch('/api/set_deck_load', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({deck_id: deckId, load_type: loadType})
                });
                
                if (response.ok) {
                    loadDecksList();
                }
            } catch (error) {
                console.error('Ошибка установки нагрузки:', error);
            }
        }
        
        // Добавление новой карточки
        async function addCard() {
            const card = {
                deck_id: document.getElementById('editorDeckSelect').value,
                chunk: document.getElementById('cardChunk').value,
                trigger: document.getElementById('cardTrigger').value,
                translation: document.getElementById('cardTranslation').value,
                dialogue: document.getElementById('cardDialogue').value,
                level: document.getElementById('cardLevel').value
            };
            
            if (!card.deck_id || !card.chunk || !card.trigger) {
                alert('Заполните обязательные поля!');
                return;
            }
            
            try {
                const response = await fetch('/api/add_card', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(card)
                });
                
                const data = await response.json();
                if (data.success) {
                    alert('Карточка добавлена!');
                    // Очищаем форму
                    document.getElementById('cardChunk').value = '';
                    document.getElementById('cardTrigger').value = '';
                    document.getElementById('cardTranslation').value = '';
                    document.getElementById('cardDialogue').value = '';
                }
            } catch (error) {
                console.error('Ошибка добавления карточки:', error);
            }
        }
        
        // Синхронизация карточек
        async function syncCards() {
            const deckId = document.getElementById('managerDeckSelect').value;
            
            document.getElementById('syncResult').innerHTML = '<span style="color: #1976d2;">⏳ Синхронизация...</span>';
            
            try {
                const response = await fetch('/api/sync_cards', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({deck_id: deckId})
                });
                
                const data = await response.json();
                
                if (data.success) {
                    const stats = data.stats;
                    let resultHtml = '<div style="color: #2e7d32;">';
                    resultHtml += `✅ Синхронизация завершена<br>`;
                    
                    if (stats.new_files > 0) {
                        resultHtml += `📄 Новых файлов: ${stats.new_files}<br>`;
                    }
                    if (stats.updated_files > 0) {
                        resultHtml += `📝 Обновлено файлов: ${stats.updated_files}<br>`;
                    }
                    if (stats.new_cards > 0) {
                        resultHtml += `✨ Новых карточек: ${stats.new_cards}<br>`;
                    }
                    if (stats.updated_cards > 0) {
                        resultHtml += `♻️ Обновлено карточек: ${stats.updated_cards}<br>`;
                    }
                    
                    if (stats.errors && stats.errors.length > 0) {
                        resultHtml += '<div style="color: #d32f2f; margin-top: 5px;">⚠️ Ошибки:<br>';
                        stats.errors.forEach(err => {
                            resultHtml += `- ${err}<br>`;
                        });
                        resultHtml += '</div>';
                    }
                    
                    if (stats.new_files === 0 && stats.updated_files === 0) {
                        resultHtml = '<div style="color: #666;">📍 Нет изменений в файлах</div>';
                    }
                    
                    resultHtml += '</div>';
                    document.getElementById('syncResult').innerHTML = resultHtml;
                    
                    // Обновляем список карточек если открыт поиск
                    if (document.getElementById('searchInput').value) {
                        searchCards();
                    }
                    
                    // Обновляем статус
                    updateSyncStatus();
                } else {
                    document.getElementById('syncResult').innerHTML = 
                        `<span style="color: #d32f2f;">❌ Ошибка: ${data.error || 'Неизвестная ошибка'}</span>`;
                }
            } catch (error) {
                document.getElementById('syncResult').innerHTML = 
                    `<span style="color: #d32f2f;">❌ Ошибка соединения</span>`;
            }
        }
        
        async function updateSyncStatus() {
            try {
                const response = await fetch('/api/sync_status');
                const data = await response.json();
                
                if (data.files && data.files.length > 0) {
                    const lastSync = new Date(data.files[0].last_imported);
                    const timeAgo = getTimeAgo(lastSync);
                    document.getElementById('syncStatus').innerHTML = 
                        `📊 Файлов в системе: ${data.total_files} | Последняя синхронизация: ${timeAgo}`;
                } else {
                    document.getElementById('syncStatus').innerHTML = 
                        '📊 Нет синхронизированных файлов';
                }
            } catch (error) {
                console.error('Ошибка получения статуса:', error);
            }
        }
        
        function getTimeAgo(date) {
            const seconds = Math.floor((new Date() - date) / 1000);
            
            if (seconds < 60) return 'только что';
            if (seconds < 3600) return Math.floor(seconds / 60) + ' мин назад';
            if (seconds < 86400) return Math.floor(seconds / 3600) + ' ч назад';
            return Math.floor(seconds / 86400) + ' дн назад';
        }
        
        function toggleAutoSync() {
            autoSyncEnabled = !autoSyncEnabled;
            
            if (autoSyncEnabled) {
                // Включаем автосинхронизацию каждые 30 секунд
                autoSyncInterval = setInterval(() => {
                    syncCards();
                }, 30000);
                
                document.getElementById('autoSyncBtn').innerHTML = '⚡ Авто-синхронизация: ВКЛ';
                document.getElementById('autoSyncBtn').style.background = '#4caf50';
                
                // Сразу синхронизируем
                syncCards();
            } else {
                // Выключаем
                if (autoSyncInterval) {
                    clearInterval(autoSyncInterval);
                }
                
                document.getElementById('autoSyncBtn').innerHTML = '⚡ Авто-синхронизация: ВЫКЛ';
                document.getElementById('autoSyncBtn').style.background = '#6c757d';
            }
        }
        
        // Поиск карточек (для менеджера)
        async function searchCards() {
            const query = document.getElementById('searchInput').value;
            if (!query) {
                showManagerMessage('Введите текст для поиска', 'error');
                return;
            }
            
            try {
                const response = await fetch(`/api/search_cards?q=${encodeURIComponent(query)}`);
                const cards = await response.json();
                displayCards(cards);
            } catch (error) {
                showManagerMessage('Ошибка поиска: ' + error, 'error');
            }
        }
        
        // Отображение списка карточек
        function displayCards(cards) {
            const container = document.getElementById('cardsList');
            
            if (cards.length === 0) {
                container.innerHTML = '<div style="padding: 20px; text-align: center; color: #999; font-size: 13px;">Карточки не найдены</div>';
                return;
            }
            
            container.innerHTML = cards.map(card => `
                <div class="card-item" onclick="selectCardForEdit(${card.id})">
                    <div class="card-item-header">
                        <span class="card-chunk">${card.chunk}</span>
                        <span class="card-level">${card.level}</span>
                    </div>
                    <div class="card-translation">${card.translation}</div>
                </div>
            `).join('');
        }
        
        // Выбор карточки для редактирования
        async function selectCardForEdit(cardId) {
            try {
                const response = await fetch(`/api/get_card/${cardId}`);
                const card = await response.json();
                
                currentEditCardId = cardId;
                
                // Заполняем форму
                document.getElementById('editCardId').value = card.id;
                document.getElementById('editChunk').value = card.chunk;
                document.getElementById('editTrigger').value = card.trigger;
                document.getElementById('editTranslation').value = card.translation;
                document.getElementById('editDialogue').value = card.dialogue;
                document.getElementById('editLevel').value = card.level;
                document.getElementById('managerDeckSelect').value = card.deck_id || '';
                
                // Подсвечиваем выбранную карточку
                document.querySelectorAll('.card-item').forEach(item => {
                    item.classList.remove('selected');
                });
                event.currentTarget.classList.add('selected');
            } catch (error) {
                showManagerMessage('Ошибка загрузки карточки: ' + error, 'error');
            }
        }
        
        // Обновление карточки
        async function updateCard() {
            if (!currentEditCardId) {
                showManagerMessage('Выберите карточку для редактирования', 'error');
                return;
            }
            
            const data = {
                chunk: document.getElementById('editChunk').value,
                trigger: document.getElementById('editTrigger').value,
                translation: document.getElementById('editTranslation').value,
                dialogue: document.getElementById('editDialogue').value,
                level: document.getElementById('editLevel').value,
                deck_id: document.getElementById('managerDeckSelect').value
            };
            
            try {
                const response = await fetch(`/api/update_card/${currentEditCardId}`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });
                
                const result = await response.json();
                if (result.success) {
                    showManagerMessage('Карточка сохранена!', 'success');
                    searchCards(); // Обновляем список
                }
            } catch (error) {
                showManagerMessage('Ошибка сохранения: ' + error, 'error');
            }
        }
        
        // Сброс прогресса карточки
        async function resetCard() {
            if (!currentEditCardId) {
                showManagerMessage('Выберите карточку', 'error');
                return;
            }
            
            if (!confirm('Сбросить прогресс изучения этой карточки?')) return;
            
            try {
                const response = await fetch(`/api/reset_card/${currentEditCardId}`, {
                    method: 'POST'
                });
                
                const result = await response.json();
                if (result.success) {
                    showManagerMessage('Прогресс сброшен!', 'success');
                }
            } catch (error) {
                showManagerMessage('Ошибка сброса: ' + error, 'error');
            }
        }
        
        // Удаление карточки
        async function deleteCard() {
            if (!currentEditCardId) {
                showManagerMessage('Выберите карточку для удаления', 'error');
                return;
            }
            
            if (!confirm('Удалить эту карточку?')) return;
            
            try {
                const response = await fetch(`/api/delete_card/${currentEditCardId}`, {
                    method: 'DELETE'
                });
                
                const result = await response.json();
                if (result.success) {
                    showManagerMessage('Карточка удалена!', 'success');
                    currentEditCardId = null;
                    document.getElementById('editChunk').value = '';
                    document.getElementById('editTrigger').value = '';
                    document.getElementById('editTranslation').value = '';
                    document.getElementById('editDialogue').value = '';
                    searchCards();
                }
            } catch (error) {
                showManagerMessage('Ошибка удаления: ' + error, 'error');
            }
        }
        
        // Массовый импорт
        async function bulkImport() {
            const deckId = document.getElementById('importDeckSelect').value;
            const text = document.getElementById('bulkImportText').value;
            
            if (!deckId || !text) {
                showManagerMessage('Выберите колоду и введите карточки!', 'error');
                return;
            }
            
            try {
                const response = await fetch('/api/bulk_import', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        deck_id: deckId,
                        cards_text: text
                    })
                });
                
                const result = await response.json();
                if (result.success) {
                    let msg = `Импортировано: ${result.imported} карточек`;
                    if (result.errors && result.errors.length > 0) {
                        msg += '\\n\\nОшибки:\\n' + result.errors.join('\\n');
                    }
                    alert(msg);
                    document.getElementById('bulkImportText').value = '';
                    loadDecks();
                }
            } catch (error) {
                showManagerMessage('Ошибка импорта: ' + error, 'error');
            }
        }
        
        // Показать сообщение в менеджере
        function showManagerMessage(text, type) {
            const msg = document.getElementById('managerMessage');
            msg.textContent = text;
            msg.className = 'message ' + type;
            msg.style.display = 'block';
            
            setTimeout(() => {
                msg.style.display = 'none';
            }, 3000);
        }
        
        // Смена колоды
        function changeDeck() {
            currentDeckId = document.getElementById('deckSelector').value;
            localStorage.setItem('currentDeckId', currentDeckId);
            
            // Находим выбранную колоду и применяем её настройки нагрузки
            const deck = allDecks.find(d => d.id == currentDeckId);
            if (deck && deck.load_type) {
                userLoad = deck.load_type;
                localStorage.setItem('userLoad', userLoad);
            }
            
            loadNextCard();
        }
        
        // Обновление прогресса
        function updateProgress() {
            const settings = loadSettings[userLoad];
            
            // Ограничиваем отображение максимальными значениями
            const displayNew = Math.min(todayStats.new, settings.daily_new);
            const displayReview = Math.min(todayStats.review, settings.daily_review);
            
            document.getElementById('progressText').textContent = 
                `Сегодня: ${displayNew}/${settings.daily_new} новых, ${displayReview}/${settings.daily_review} повторений`;
            
            const totalDone = todayStats.new + todayStats.review;
            const totalNeeded = settings.daily_new + settings.daily_review;
            const percentage = Math.min(100, (totalDone / totalNeeded) * 100);
            
            const progressFill = document.getElementById('progressFill');
            progressFill.style.width = percentage + '%';
            
            // Меняем цвет прогресс-бара в зависимости от выполнения
            if (percentage >= 100) {
                progressFill.style.background = '#4caf50'; // Зеленый - выполнено
            } else if (percentage >= 75) {
                progressFill.style.background = '#ffc107'; // Желтый - почти выполнено
            } else {
                progressFill.style.background = '#2196f3'; // Синий - в процессе
            }
        }
        
        // Загрузка следующей карточки
        async function loadNextCard() {
            try {
                const response = await fetch('/api/next_card?load=' + userLoad + '&deck_id=' + currentDeckId);
                const data = await response.json();
                
                if (data.stats) {
                    todayStats = data.stats;
                    updateProgress();
                    
                    // Проверяем, достигнуты ли лимиты
                    const settings = loadSettings[userLoad];
                    const newLimitReached = todayStats.new >= settings.daily_new;
                    const reviewLimitReached = todayStats.review >= settings.daily_review;
                    
                    // Обновляем отображение счетчиков с учетом лимитов
                    const newCount = document.getElementById('newCount');
                    const reviewCount = document.getElementById('reviewCount');
      
                    // Подсвечиваем красным, если лимит достигнут
                    if (newLimitReached) {
                        newCount.style.color = '#ef5350';
                        newCount.textContent = `${todayStats.new}/${settings.daily_new}`;
                    } else {
                        newCount.style.color = '#7986cb';
                        newCount.textContent = todayStats.new;
                    }
                    
                    if (reviewLimitReached) {
                        reviewCount.style.color = '#ef5350';
                        reviewCount.textContent = `${todayStats.review}/${settings.daily_review}`;
                    } else {
                        reviewCount.style.color = '#7986cb';
                        reviewCount.textContent = todayStats.review;
                    }
                }
                
                if (data.card) {
                    currentCard = data.card;
                    displayCard(data.card);
                } else if (data.limit_reached) {
                    showLimitReached();
                } else {
                    showNoCards();
                }
                
                updateStats();
            } catch (error) {
                console.error('Ошибка:', error);
                document.getElementById('cardContent').innerHTML = `
                    <div style="text-align: center; padding: 50px;">
                        <h3 style="color: #ef5350;">⚠️ Ошибка загрузки</h3>
                        <p style="margin-top: 20px; color: #666;">Не удалось загрузить карточку</p>
                        <button onclick="location.reload()" style="margin-top: 20px; background: #90caf9; color: white; border: none; padding: 12px 30px; border-radius: 20px; cursor: pointer;">
                            Обновить страницу
                        </button>
                    </div>
                `;
            }
        }
        
        // Отображение карточки
        function displayCard(card) {
            showingAnswer = false;
            document.getElementById('cardLevel').textContent = `УРОВЕНЬ ${card.level}`;
            
            let situationText = '';
            let dialogueHtml = '';
            
            if (card.trigger.includes('[') && card.trigger.includes(']')) {
    const match = card.trigger.match(/\\[(.+?)\\](.+)/);
                if (match) {
                    situationText = match[1];
                    const dialoguePart = match[2].trim();
                    
                    const lines = dialoguePart.split('—').filter(line => line.trim());
                    dialogueHtml = lines.map(line => {
                        const formattedLine = line.trim().replace('______', '<span class="gap-placeholder"></span>');
                        return `<div class="dialogue-line">—${formattedLine}</div>`;
                    }).join('');
                }
            } else {
                const formattedLine = card.trigger.replace('______', '<span class="gap-placeholder"></span>');
                dialogueHtml = `<div class="dialogue-line">${formattedLine}</div>`;
            }
            
            document.getElementById('cardContent').innerHTML = `
                ${situationText ? `
                    <div class="situation-card">
                        <div class="situation-label">Ситуация</div>
                        <div class="situation-text">${situationText}</div>
                    </div>
                ` : ''}
                
                <div class="dialogue-container">
                    ${dialogueHtml}
                </div>
            console.log('Front audio:', card.front_audio);
                ${card.front_audio ? `
                <button class="audio-button" onclick="playAudio('${card.front_audio}')">
                    🔊
                </button>
            ` : ''}
            <button class="show-answer-btn" onclick="showAnswer()">Показать ответ</button>
                
            `;
        }
        
        // Показать ответ
        function showAnswer() {
            if (!currentCard || showingAnswer) return;
            showingAnswer = true;
            
            let fullDialogue = currentCard.dialogue.split('—').filter(line => line.trim())
                .map(line => `<div class="dialogue-line">—${line.trim()}</div>`).join('');
            
            document.getElementById('cardContent').innerHTML = `
                <div class="main-phrase">${currentCard.chunk}</div>
                
                ${currentCard.audio_path ? `
                    <button class="audio-button" onclick="playAudio('${currentCard.audio_path}')">
                        🔊
                    </button>
                ` : ''}
                
                <div class="translation">${currentCard.translation}</div>
                
                <div class="dialogue-container">
                    ${fullDialogue}
                </div>
                
                <div class="answer-buttons">
                    <button class="rate-btn btn-again" onclick="rateCard(1)">
                        Снова<small>10м</small>
                    </button>
                    <button class="rate-btn btn-hard" onclick="rateCard(2)">
                        Трудно<small>1ч</small>
                    </button>
                    <button class="rate-btn btn-good" onclick="rateCard(3)">
                        Хорошо<small>1д</small>
                    </button>
                    <button class="rate-btn btn-easy" onclick="rateCard(4)">
                        Легко<small>4д</small>
                    </button>
                </div>
            `;
        }
        
        // Оценка карточки
        async function rateCard(rating) {
            if (!currentCard || !showingAnswer) return;
            
            try {
                const response = await fetch('/api/review', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        card_id: currentCard.id,
                        rating: rating
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    // Не увеличиваем счетчики вручную - получим актуальные данные с сервера
                    loadNextCard();
                }
            } catch (error) {
                console.error('Ошибка:', error);
            }
        }
        
        // Обновление статистики
        async function updateStats() {
            try {
                const response = await fetch('/api/stats');
                const stats = await response.json();
                document.getElementById('newCount').textContent = stats.new;
                document.getElementById('learningCount').textContent = stats.learning;
                document.getElementById('reviewCount').textContent = stats.review;
            } catch (error) {
                console.error('Ошибка статистики:', error);
            }
        }
        
        // Показать сообщение о достижении лимита
        function showLimitReached() {
            const settings = loadSettings[userLoad];
            const allDone = todayStats.new >= settings.daily_new && todayStats.review >= settings.daily_review;
            
            document.getElementById('cardContent').innerHTML = `
                <div style="text-align: center; padding: 50px;">
                    <h2 style="color: ${allDone ? '#4caf50' : '#ffa726'};">
                        ${allDone ? '✅ Дневная цель выполнена!' : '🎯 Дневной лимит достигнут!'}
                    </h2>
                    <p style="margin-top: 20px; font-size: 18px;">
                        Изучено новых: <strong>${todayStats.new}/${settings.daily_new}</strong>
                    </p>
                    <p style="font-size: 18px;">
                        Повторено: <strong>${todayStats.review}/${settings.daily_review}</strong>
                    </p>
                    <p style="margin-top: 20px; color: #666;">
                        Режим: ${TIME_SETTINGS[userLoad].name} (${settings.minutes} минут/день)
                    </p>
                    
                    ${!allDone ? `
                        <div style="margin-top: 30px; padding: 15px; background: #fff3cd; border-radius: 10px;">
                            <p style="color: #856404; font-size: 14px;">
                                💡 Совет: Карточки для повторения появятся позже сегодня 
                                или завтра в зависимости от алгоритма FSRS
                            </p>
                        </div>
                    ` : ''}
                    
                    <button onclick="location.reload()" style="margin-top: 30px; background: #90caf9; color: white; border: none; padding: 12px 30px; border-radius: 20px; cursor: pointer;">
                        🔄 Обновить
                    </button>
                    
                    ${allDone ? `
                        <button onclick="changeLoadSettings()" style="margin-top: 10px; background: #66bb6a; color: white; border: none; padding: 12px 30px; border-radius: 20px; cursor: pointer;">
                            ⚡ Увеличить нагрузку
                        </button>
                    ` : ''}
                </div>
            `;
        }
        
        // Показать сообщение об отсутствии карточек
        function showNoCards() {
            document.getElementById('cardContent').innerHTML = `
                <div style="text-align: center; padding: 50px;">
                    <h2 style="color: #81c784;">✨ Все карточки изучены!</h2>
                    <p style="margin-top: 20px; color: #666;">Нет карточек для повторения.</p>
                    <button onclick="location.reload()" style="margin-top: 20px; background: #90caf9; color: white; border: none; padding: 12px 30px; border-radius: 20px; cursor: pointer;">
                        Обновить
                    </button>
                </div>
            `;
        }
        
        // Изменение настроек нагрузки
        function changeLoadSettings() {
            const loads = ['light', 'normal', 'intensive'];
            const currentIndex = loads.indexOf(userLoad);
            const nextIndex = (currentIndex + 1) % loads.length;
            const nextLoad = loads[nextIndex];
            
            if (confirm(`Переключить на режим "${TIME_SETTINGS[nextLoad].name}"?`)) {
                userLoad = nextLoad;
                localStorage.setItem('userLoad', userLoad);
                location.reload();
            }
        }
        
        // Воспроизведение аудио
        function playAudio(path) {
            const audio = new Audio('/media/' + path);
            audio.play();
        }
        
        // Инициализация при загрузке
        window.onload = function() {
            loadDecks();
            loadNextCard();
            updateProgress();
        }
    </script>
</body>
</html>'''

# === ОСТАЛЬНАЯ ЧАСТЬ КОДА PYTHON ===

@app.route('/api/get_card/<int:card_id>')
def get_card(card_id):
    """Получить данные конкретной карточки для редактирования"""
    card = CardModel.query.get(card_id)
    if not card:
        return jsonify({'error': 'Card not found'}), 404
    
    deck_card = DeckCardModel.query.filter_by(card_id=card_id).first()
    
    return jsonify({
        'id': card.id,
        'chunk': card.chunk,
        'trigger': card.trigger,
        'translation': card.translation,
        'dialogue': card.dialogue,
        'level': card.level,
        'deck_id': deck_card.deck_id if deck_card else None,
        'state': card.state,
        'reps': card.reps
    })

@app.route('/api/update_card/<int:card_id>', methods=['POST'])
def update_card(card_id):
    """Обновить существующую карточку"""
    card = CardModel.query.get(card_id)
    if not card:
        return jsonify({'error': 'Card not found'}), 404
    
    data = request.json
    
    card.chunk = data.get('chunk', card.chunk)
    card.trigger = data.get('trigger', card.trigger)
    card.translation = data.get('translation', card.translation)
    card.dialogue = data.get('dialogue', card.dialogue)
    card.level = data.get('level', card.level)
    
    new_deck_id = data.get('deck_id')
    if new_deck_id:
        DeckCardModel.query.filter_by(card_id=card_id).delete()
        deck_card = DeckCardModel(
            deck_id=new_deck_id,
            card_id=card_id
        )
        db.session.add(deck_card)
    
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Карточка обновлена'})

@app.route('/api/delete_card/<int:card_id>', methods=['DELETE'])
def delete_card(card_id):
    """Удалить карточку"""
    card = CardModel.query.get(card_id)
    if not card:
        return jsonify({'error': 'Card not found'}), 404
    
    DeckCardModel.query.filter_by(card_id=card_id).delete()
    db.session.delete(card)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Карточка удалена'})

@app.route('/api/search_cards')
def search_cards():
    """Поиск карточек по фразе или переводу"""
    query = request.args.get('q', '')
    deck_id = request.args.get('deck_id', type=int)
    
    if not query:
        return jsonify([])
    
    cards_query = CardModel.query.filter(
        db.or_(
            CardModel.chunk.contains(query),
            CardModel.translation.contains(query)
        )
    )
    
    if deck_id:
        cards_query = cards_query.join(DeckCardModel).filter(
            DeckCardModel.deck_id == deck_id
        )
    
    cards = cards_query.limit(20).all()
    
    result = []
    for card in cards:
        deck_card = DeckCardModel.query.filter_by(card_id=card.id).first()
        result.append({
            'id': card.id,
            'chunk': card.chunk,
            'translation': card.translation,
            'level': card.level,
            'deck_id': deck_card.deck_id if deck_card else None
        })
    
    return jsonify(result)

@app.route('/api/reset_card/<int:card_id>', methods=['POST'])
def reset_card(card_id):
    """Сбросить прогресс карточки (начать изучение заново)"""
    card = CardModel.query.get(card_id)
    if not card:
        return jsonify({'error': 'Card not found'}), 404
    
    card.due = datetime.now(timezone.utc)
    card.stability = 0
    card.difficulty = 0
    card.elapsed_days = 0
    card.scheduled_days = 0
    card.reps = 0
    card.lapses = 0
    card.state = 0
    card.last_review = None
    
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Прогресс карточки сброшен'})

@app.route('/api/bulk_import', methods=['POST'])
def bulk_import():
    """Массовый импорт карточек из текстового формата"""
    data = request.json
    deck_id = data.get('deck_id')
    cards_text = data.get('cards_text', '')
    
    if not deck_id or not cards_text:
        return jsonify({'error': 'Необходимо указать колоду и текст карточек'}), 400
    
    imported = 0
    errors = []
    
    for line_num, line in enumerate(cards_text.split('\n'), 1):
        line = line.strip()
        if not line:
            continue
            
        parts = line.split('|')
        if len(parts) < 7:
            errors.append(f'Строка {line_num}: недостаточно полей (нужно 7)')
            continue
        
        try:
            existing = CardModel.query.filter_by(chunk=parts[0]).first()
            if existing:
                errors.append(f'Строка {line_num}: фраза "{parts[0]}" уже существует')
                continue
            
            card = CardModel(
                chunk=parts[0],
                trigger=parts[1],
                translation=parts[2],
                dialogue=parts[3],
                audio_path=parts[4].replace('[sound:', '').replace(']', '') if parts[4] != '[sound:]' else '',
                image_path=parts[5].replace('[img:', '').replace(']', ''),
                level=parts[6],
                state=0,
                due=datetime.now(timezone.utc)
            )
            db.session.add(card)
            db.session.flush()
            
            deck_card = DeckCardModel(
                deck_id=deck_id,
                card_id=card.id
            )
            db.session.add(deck_card)
            imported += 1
            
        except Exception as e:
            errors.append(f'Строка {line_num}: {str(e)}')
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'imported': imported,
        'errors': errors
    })

@app.route('/api/sync_cards', methods=['POST'])
def sync_cards():
    """Синхронизировать карточки из папки data"""
    data = request.json
    deck_id = data.get('deck_id')
    
    data_dir = os.path.join(os.path.dirname(__file__), 'data')
    
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
        return jsonify({'error': 'Папка data создана, поместите туда файлы'}), 400
    
    stats = sync_cards_from_directory(data_dir, deck_id)
    
    return jsonify({
        'success': True,
        'stats': stats,
        'message': f"Новых файлов: {stats['new_files']}, Обновлено файлов: {stats['updated_files']}, "
                   f"Новых карточек: {stats['new_cards']}, Обновлено карточек: {stats['updated_cards']}"
    })

@app.route('/api/sync_status')
def sync_status():
    """Получить статус синхронизации"""
    files = FileTrackingModel.query.order_by(FileTrackingModel.last_imported.desc()).all()
    
    return jsonify({
        'files': [{
            'filename': f.filename,
            'last_imported': f.last_imported.isoformat() if f.last_imported else None,
            'cards_count': f.cards_count
        } for f in files],
        'total_files': len(files)
    })

# Замените функцию get_next_card() на эту исправленную версию:

@app.route('/api/next_card')
def get_next_card():
    """Получить следующую карточку с учетом дневных лимитов"""
    now = datetime.now(timezone.utc)
    today = now.date()
    
    user_load = request.args.get('load', 'normal')
    deck_id = request.args.get('deck_id', 1, type=int)
    settings = TIME_SETTINGS.get(user_load, TIME_SETTINGS['normal'])
    
    # Определяем начало и конец текущего дня
    today_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
    today_end = datetime.combine(today, datetime.max.time()).replace(tzinfo=timezone.utc)
    
    # Считаем карточки, изученные СЕГОДНЯ
    # Новые карточки - это те, которые были изучены сегодня И у которых reps = 1
    today_new = CardModel.query.filter(
        CardModel.last_review >= today_start,
        CardModel.last_review <= today_end,
        CardModel.reps == 1  # Первое изучение
    ).count()
    
    # Повторения - это карточки, повторенные сегодня с reps > 1
    today_review = CardModel.query.filter(
        CardModel.last_review >= today_start,
        CardModel.last_review <= today_end,
        CardModel.reps > 1  # Уже изучались ранее
    ).count()
    
    card_to_show = None
    
    # СНАЧАЛА проверяем карточки для повторения (если лимит не достигнут)
    if today_review < settings['daily_review']:
        # Ищем карточки, которые нужно повторить (due <= now и уже изучались)
        review_card = db.session.query(CardModel).join(
            DeckCardModel, CardModel.id == DeckCardModel.card_id
        ).filter(
            DeckCardModel.deck_id == deck_id,
            CardModel.due <= now,
            CardModel.state > 0  # Карточки в процессе изучения или на повторении
        ).order_by(CardModel.due).first()
        
        if review_card:
            card_to_show = review_card
    
    # Если нет карточек для повторения И лимит новых не достигнут
    if not card_to_show and today_new < settings['daily_new']:
        # Ищем новую карточку (которая еще ни разу не изучалась)
        new_card = db.session.query(CardModel).join(
            DeckCardModel, CardModel.id == DeckCardModel.card_id
        ).filter(
            DeckCardModel.deck_id == deck_id,
            CardModel.state == 0,  # Новая карточка
            CardModel.reps == 0     # Никогда не изучалась
        ).first()
        
        if new_card:
            card_to_show = new_card
    
    # Если есть карточка для показа
    if card_to_show:
        return jsonify({
            'card': card_to_show.to_dict(),
            'stats': {
                'new': today_new,
                'review': today_review,
                'total': today_new + today_review,
                'limits': {
                    'daily_new': settings['daily_new'],
                    'daily_review': settings['daily_review']
                }
            }
        })
    
    # Определяем, достигнуты ли лимиты
    limit_reached = (today_new >= settings['daily_new'] and today_review >= settings['daily_review'])
    
    return jsonify({
        'card': None,
        'limit_reached': limit_reached,
        'stats': {
            'new': today_new,
            'review': today_review,
            'total': today_new + today_review,
            'limits': {
                'daily_new': settings['daily_new'],
                'daily_review': settings['daily_review']
            }
        },
        'message': 'Дневные лимиты достигнуты' if limit_reached else 'Нет карточек для изучения'
    })


# Также обновите функцию review_card() для корректной обработки:

@app.route('/api/review', methods=['POST'])
def review_card():
    """Оценить карточку используя упрощенный алгоритм FSRS"""
    data = request.json
    card_id = data.get('card_id')
    rating_value = data.get('rating', 3)
    
    card = CardModel.query.get(card_id)
    if not card:
        return jsonify({'error': 'Card not found'}), 404
    
    now = datetime.now(timezone.utc)
    
    # Упрощенный алгоритм планирования
    if rating_value == 1:  # Again - снова через 10 минут
        card.scheduled_days = 0.007  # ~10 минут
        card.lapses += 1
        card.state = 1  # Learning
    elif rating_value == 2:  # Hard - труднее
        if card.reps == 0:
            card.scheduled_days = 0.042  # ~1 час
            card.state = 1  # Learning
        else:
            card.scheduled_days = max(0.5, card.scheduled_days * 0.6)
            card.state = 1 if card.scheduled_days < 1 else 2
    elif rating_value == 3:  # Good - хорошо
        if card.reps == 0:
            card.scheduled_days = 1  # 1 день
            card.state = 2  # Review
        else:
            # Увеличиваем интервал в зависимости от предыдущих успехов
            multiplier = 2.5 if card.lapses == 0 else 2.0
            card.scheduled_days = min(365, card.scheduled_days * multiplier)
            card.state = 2  # Review
    elif rating_value == 4:  # Easy - легко
        if card.reps == 0:
            card.scheduled_days = 4  # 4 дня
            card.state = 2  # Review
        else:
            card.scheduled_days = min(365, card.scheduled_days * 3.5)
            card.state = 2  # Review
    
    # Обновляем статистику карточки
    card.reps += 1
    card.last_review = now
    card.due = now + timedelta(days=card.scheduled_days)
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'next_review': card.due.isoformat(),
        'scheduled_days': card.scheduled_days
    })

@app.route('/api/stats')
def get_stats():
    """Статистика карточек по состояниям"""
    new = CardModel.query.filter_by(state=0).count()
    learning = CardModel.query.filter_by(state=1).count()
    review = CardModel.query.filter_by(state=2).count()
    
    return jsonify({
        'new': new,
        'learning': learning,
        'review': review
    })

@app.route('/api/decks')
def get_decks():
    """Получить список всех колод"""
    decks = DeckModel.query.all()
    result = []
    for deck in decks:
        total_cards = DeckCardModel.query.filter_by(deck_id=deck.id).count()
        deck_dict = deck.to_dict()
        deck_dict['total_cards'] = total_cards
        result.append(deck_dict)
    return jsonify(result)

@app.route('/api/create_deck', methods=['POST'])
def create_deck():
    """Создать новую колоду"""
    data = request.json
    name = data.get('name', '').strip()
    
    if not name:
        return jsonify({'success': False, 'error': 'Имя не может быть пустым'}), 400
    
    deck = DeckModel(
        name=name,
        description='',
        load_type='normal'
    )
    db.session.add(deck)
    db.session.commit()
    
    return jsonify({'success': True, 'deck_id': deck.id})

@app.route('/api/set_deck_load', methods=['POST'])
def set_deck_load():
    """Установить нагрузку для колоды"""
    data = request.json
    deck_id = data.get('deck_id')
    load_type = data.get('load_type')
    
    deck = DeckModel.query.get(deck_id)
    if deck and load_type in TIME_SETTINGS:
        deck.load_type = load_type
        deck.daily_new = TIME_SETTINGS[load_type]['daily_new']
        deck.daily_review = TIME_SETTINGS[load_type]['daily_review']
        db.session.commit()
        return jsonify({'success': True})
    
    return jsonify({'success': False}), 400

@app.route('/api/add_card', methods=['POST'])
def add_card():
    """Добавить новую карточку"""
    data = request.json
    
    card = CardModel(
        chunk=data.get('chunk'),
        trigger=data.get('trigger'),
        translation=data.get('translation'),
        dialogue=data.get('dialogue'),
        level=data.get('level', 'A2'),
        audio_path='',
        image_path='',
        state=0,
        due=datetime.now(timezone.utc)
    )
    db.session.add(card)
    db.session.flush()
    
    deck_card = DeckCardModel(
        deck_id=data.get('deck_id'),
        card_id=card.id
    )
    db.session.add(deck_card)
    db.session.commit()
    
    return jsonify({'success': True, 'card_id': card.id})

@app.route('/api/reset_daily_stats', methods=['POST'])
def reset_daily_stats():
    """Сбросить дневную статистику (для тестирования)"""
    today = datetime.now(timezone.utc).date()
    today_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
    
    cards_today = CardModel.query.filter(
        CardModel.last_review >= today_start
    ).all()
    
    for card in cards_today:
        if card.reps == 1:
            card.reps = 0
            card.state = 0
            card.last_review = None
            card.due = datetime.now(timezone.utc)
        else:
            card.last_review = card.last_review - timedelta(days=1)
    
    db.session.commit()
    
    return jsonify({'success': True, 'reset_count': len(cards_today)})

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route('/media/<path:filename>')
def serve_media(filename):
    media_dir = os.path.join(os.path.dirname(__file__), 'media')
    return send_from_directory(media_dir, filename)

# Инициализация БД при импорте модуля (для gunicorn)
with app.app_context():
    db.create_all()
    
    if not DeckModel.query.first():
        default_deck = DeckModel(
            name="Испанский A2-B2",
            description="Основная колода",
            load_type='normal'
        )
        db.session.add(default_deck)
        db.session.commit()
        print("✅ Создана базовая колода")
    
    initial_sync()

# Это остается для локального запуска
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
