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
