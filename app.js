// app.js - Основная логика приложения с FSRS и Supabase

class SpanishCardsApp {
    constructor(supabase, user) {
        this.supabase = supabase;
        this.user = user;
        
        // Извлекаем FSRS из глобального объекта
        const { FSRS, createEmptyCard, Rating } = window.FSRS;
        
        // Сохраняем функции для использования
        this.createEmptyCard = createEmptyCard;
        this.Rating = Rating;
        
        // Создаем экземпляр FSRS
        this.fsrs = new FSRS({
            w: [0.4, 0.6, 2.4, 5.8, 4.93, 0.94, 0.86, 0.01, 1.49, 0.14, 0.94, 2.18, 0.05, 0.34, 1.26, 0.29, 2.61],
            request_retention: 0.9,
            maximum_interval: 365
        });
        
        // App state
        this.cards = [];
        this.cardStates = {};
        this.currentCard = null;
        this.currentAudio = null;
        this.audioCache = new Map(); // Кеш для аудио объектов
        this.isFlipped = false;
        this.studyMode = 'mixed';
        this.settings = {
            autoplay: true,
            showIntervals: true,
            dailyNewLimit: 10,
            dailyReviewLimit: 100
        };
        this.todayStats = {
            studied: 0,
            correct: 0,
            newCards: 0,
            reviewCards: 0
        };
        
        this.init();
    }
    
    async init() {
        try {
            // Загрузка настроек пользователя
            await this.loadUserSettings();
            
            // Загрузка карточек
            await this.loadCards();
            
            // Загрузка состояний карточек
            await this.loadCardStates();
            
            // Загрузка статистики за сегодня
            await this.loadTodayStats();
            
            // Инициализация UI
            this.initializeUI();
            
            // Показать первую карточку
            this.selectNextCard();
            
        } catch (error) {
            console.error('Initialization error:', error);
            this.showError('Ошибка инициализации: ' + error.message);
        }
    }
    
    async loadUserSettings() {
        const { data, error } = await this.supabase
            .from('user_settings')
            .select('*')
            .eq('user_id', this.user.id)
            .single();
        
        if (error && error.code !== 'PGRST116') {
            throw error;
        }
        
        if (data) {
            this.settings = {
                autoplay: data.autoplay,
                showIntervals: data.show_intervals,
                dailyNewLimit: data.daily_new_limit,
                dailyReviewLimit: data.daily_review_limit
            };
        }
    }
    
    async loadCards() {
        const { data, error } = await this.supabase
            .from('cards')
            .select('*')
            .order('id');
        
        if (error) throw error;
        
        this.cards = data;
        console.log(`Loaded ${this.cards.length} cards`);
    }
    
    async loadCardStates() {
        const { data, error } = await this.supabase
            .from('user_card_states')
            .select('*')
            .eq('user_id', this.user.id);
        
        if (error) throw error;
        
        // Конвертируем в FSRS карточки
        data.forEach(state => {
            const fsrsCard = {
                due: new Date(state.due),
                stability: state.stability,
                difficulty: state.difficulty,
                elapsed_days: state.elapsed_days,
                scheduled_days: state.scheduled_days,
                reps: state.reps,
                lapses: state.lapses,
                state: state.state,
                last_review: state.last_review ? new Date(state.last_review) : null
            };
            this.cardStates[state.card_id] = fsrsCard;
        });
        
        // Инициализация новых карточек
        this.cards.forEach(card => {
            if (!this.cardStates[card.id]) {
                this.cardStates[card.id] = this.createEmptyCard();
            }
        });
    }
    
    async loadTodayStats() {
        const today = new Date().toISOString().split('T')[0];
        
        try {
            const { data, error } = await this.supabase
                .from('user_stats')
                .select('*')
                .eq('user_id', this.user.id)
                .eq('date', today)
                .maybeSingle();
            
            if (error) {
                console.warn('Error loading stats:', error);
                return;
            }
            
            if (data) {
                this.todayStats = {
                    studied: data.studied,
                    correct: data.correct,
                    newCards: data.new_cards,
                    reviewCards: data.review_cards
                };
            }
        } catch (e) {
            console.warn('Stats loading failed, using defaults:', e);
        }
    }
    
    selectNextCard() {
        const now = new Date();
        const availableCards = [];
        
        // Сначала карточки на повторение
        if (this.todayStats.reviewCards < this.settings.dailyReviewLimit) {
            this.cards.forEach(card => {
                const state = this.cardStates[card.id];
                if (state.state !== 0 && state.due <= now) {
                    availableCards.push({ card, priority: 1 });
                }
            });
        }
        
        // Затем новые карточки
        if (availableCards.length === 0 && this.todayStats.newCards < this.settings.dailyNewLimit) {
            this.cards.forEach(card => {
                const state = this.cardStates[card.id];
                if (state.state === 0) {
                    availableCards.push({ card, priority: 2 });
                }
            });
        }
        
        if (availableCards.length === 0) {
            this.showCompletion();
            return;
        }
        
        // Выбираем случайную карточку
        const selected = availableCards[Math.floor(Math.random() * availableCards.length)];
        this.currentCard = selected.card;
        this.showCard();
    }
    
    showCard() {
        console.log('showCard called for:', this.currentCard.id, this.currentCard.spanish);
        
        // ВАЖНО: Сбрасываем состояние
        this.isFlipped = false;
        
        // Останавливаем ВСЕ аудио
        this.stopAllAudio();
        
        // Определяем направление
        const cardType = this.getCardType();
        this.currentCard.currentType = cardType;
        
        console.log('Card type:', cardType);
        
        // Обновляем UI
        const cardContent = document.getElementById('cardContent');
        const playBtn = document.getElementById('playBtn');
        const cardHint = document.getElementById('cardHint');
        const ratingButtons = document.getElementById('ratingButtons');
        
        // Сбрасываем классы и состояние кнопок
        ratingButtons.classList.remove('visible');
        cardHint.style.display = 'block';
        
        if (cardType === 'spanish-russian') {
            cardContent.textContent = this.currentCard.spanish;
            cardContent.className = 'card-content spanish';
            playBtn.innerHTML = '<span>🔊</span><span>Воспроизвести</span>';
            playBtn.style.display = this.currentCard.audio ? 'inline-flex' : 'none';
        } else {
            cardContent.textContent = this.currentCard.russian;
            cardContent.className = 'card-content russian';
            playBtn.innerHTML = '<span>🔊</span><span>Произнести</span>';
            playBtn.style.display = 'none';
        }
        
        // Подготавливаем FSRS scheduling
        const state = this.cardStates[this.currentCard.id];
        const schedulingInfo = this.fsrs.repeat(state, new Date());
        
        // FSRS v4 возвращает объект с ключами Rating
        this.currentScheduling = {
            'again': schedulingInfo[this.Rating.Again],
            'hard': schedulingInfo[this.Rating.Hard],
            'good': schedulingInfo[this.Rating.Good],
            'easy': schedulingInfo[this.Rating.Easy]
        };
        
        this.updateIntervals();
        this.updateStats();
    }
    
    getCardType() {
        if (this.studyMode === 'mixed') {
            return Math.random() < 0.7 ? 'russian-spanish' : 'spanish-russian';
        }
        return this.studyMode;
    }
    
    showAnswer() {
        if (this.isFlipped) {
            console.log('Already flipped, ignoring');
            return;
        }
        
        console.log('showAnswer called for card:', this.currentCard.id, this.currentCard.spanish);
        
        this.isFlipped = true;
        
        // Останавливаем ВСЕ аудио перед показом ответа
        this.stopAllAudio();
        
        const cardContent = document.getElementById('cardContent');
        const cardHint = document.getElementById('cardHint');
        const ratingButtons = document.getElementById('ratingButtons');
        const playBtn = document.getElementById('playBtn');
        
        if (this.currentCard.currentType === 'spanish-russian') {
            console.log('Showing Russian for:', this.currentCard.spanish, '->', this.currentCard.russian);
            cardContent.textContent = this.currentCard.russian;
            cardContent.className = 'card-content russian';
            playBtn.style.display = 'none';
        } else {
            console.log('Showing Spanish for:', this.currentCard.russian, '->', this.currentCard.spanish);
            cardContent.textContent = this.currentCard.spanish;
            cardContent.className = 'card-content spanish';
            playBtn.style.display = this.currentCard.audio ? 'inline-flex' : 'none';
            
            // Автовоспроизведение только после клика пользователя
            if (this.settings.autoplay && this.currentCard.audio) {
                setTimeout(() => {
                    // Проверяем, что все еще на той же карточке
                    if (this.isFlipped && this.currentCard.currentType === 'russian-spanish') {
                        this.playAudio();
                    }
                }, 300);
            }
        }
        
        cardHint.style.display = 'none';
        ratingButtons.classList.add('visible');
    }
    
    async rateCard(rating) {
        // Получаем старое состояние для проверки
        const oldState = this.cardStates[this.currentCard.id];
        const wasNew = oldState.state === 0; // 0 = New в FSRS
        
        // Используем правильные значения Rating из FSRS
        const ratingMap = {
            1: this.Rating.Again,
            2: this.Rating.Hard, 
            3: this.Rating.Good,
            4: this.Rating.Easy
        };
        
        const fsrsRating = ratingMap[rating];
        if (fsrsRating === undefined) {
            console.error('Invalid rating:', rating);
            return;
        }
        
        // Получаем новое состояние карточки
        const schedulingInfo = this.fsrs.repeat(oldState, new Date());
        const newCard = schedulingInfo[fsrsRating].card;
        
        if (!newCard) {
            console.error('No scheduling info for rating:', fsrsRating);
            return;
        }
        
        this.cardStates[this.currentCard.id] = newCard;
        
        // Обновляем статистику
        this.todayStats.studied++;
        if (rating >= 3) this.todayStats.correct++;
        if (wasNew && newCard.state !== 0) {
            this.todayStats.newCards++;
        } else if (!wasNew) {
            this.todayStats.reviewCards++;
        }
        
        // Сохраняем в базу данных
        await this.saveCardState(this.currentCard.id, newCard);
        await this.saveTodayStats();
        
        // Следующая карточка
        setTimeout(() => this.selectNextCard(), 300);
    }
    
    async saveCardState(cardId, fsrsCard) {
        const { error } = await this.supabase
            .from('user_card_states')
            .upsert({
                user_id: this.user.id,
                card_id: cardId,
                due: fsrsCard.due.toISOString(),
                stability: fsrsCard.stability,
                difficulty: fsrsCard.difficulty,
                elapsed_days: fsrsCard.elapsed_days,
                scheduled_days: fsrsCard.scheduled_days,
                reps: fsrsCard.reps,
                lapses: fsrsCard.lapses,
                state: fsrsCard.state,
                last_review: fsrsCard.last_review ? fsrsCard.last_review.toISOString() : null
            });
        
        if (error) {
            console.error('Error saving card state:', error);
        }
    }
    
    async saveTodayStats() {
        const today = new Date().toISOString().split('T')[0];
        
        try {
            console.log('Saving stats:', {
                user_id: this.user.id,
                date: today,
                stats: this.todayStats
            });
            
            const { data, error } = await this.supabase
                .from('user_stats')
                .upsert({
                    user_id: this.user.id,
                    date: today,
                    studied: this.todayStats.studied,
                    correct: this.todayStats.correct,
                    new_cards: this.todayStats.newCards,
                    review_cards: this.todayStats.reviewCards
                }, {
                    onConflict: 'user_id,date'
                })
                .select();
            
            if (error) {
                console.error('Error saving stats:', error);
            } else {
                console.log('Stats saved successfully:', data);
            }
        } catch (e) {
            console.error('Failed to save stats:', e);
        }
    }
    
    stopAllAudio() {
        // Останавливаем текущее аудио
        if (this.currentAudio && !this.currentAudio.paused) {
            this.currentAudio.pause();
            this.currentAudio.currentTime = 0;
            this.currentAudio = null;
        }
        
        // Останавливаем все кешированные аудио
        this.audioCache.forEach(audio => {
            if (!audio.paused) {
                audio.pause();
                audio.currentTime = 0;
            }
        });
    }
    
    playAudio() {
        if (!this.currentCard || !this.currentCard.audio) return;
        
        // Останавливаем все другие аудио
        this.stopAllAudio();
        
        const playBtn = document.getElementById('playBtn');
        playBtn.classList.add('playing');
        
        // Создаем правильный URL для текущей карточки
        const audioUrl = `${this.supabase.storageUrl}/object/public/audio/${this.currentCard.audio}`;
        
        console.log('Playing audio for card:', this.currentCard.id, 'file:', this.currentCard.audio);
        
        // Создаем новый Audio объект для этой карточки
        this.currentAudio = new Audio(audioUrl);
        
        this.currentAudio.play()
            .then(() => {
                this.currentAudio.addEventListener('ended', () => {
                    playBtn.classList.remove('playing');
                    this.currentAudio = null;
                });
            })
            .catch(error => {
                console.error('Audio playback error:', error);
                playBtn.classList.remove('playing');
                this.currentAudio = null;
            });
    }
    
    updateIntervals() {
        if (!this.settings.showIntervals || !this.currentScheduling) return;
        
        const formatInterval = (card) => {
            const days = Math.ceil((card.due - new Date()) / (1000 * 60 * 60 * 24));
            if (days < 1) return '10м';
            if (days === 1) return '1д';
            if (days < 30) return `${days}д`;
            if (days < 365) return `${Math.round(days/30)}м`;
            return `${Math.round(days/365)}г`;
        };
        
        // Проверяем, что scheduling существует и содержит нужные данные
        if (this.currentScheduling['again'] && this.currentScheduling['again'].card) {
            document.getElementById('againInterval').textContent = formatInterval(this.currentScheduling['again'].card);
        }
        if (this.currentScheduling['hard'] && this.currentScheduling['hard'].card) {
            document.getElementById('hardInterval').textContent = formatInterval(this.currentScheduling['hard'].card);
        }
        if (this.currentScheduling['good'] && this.currentScheduling['good'].card) {
            document.getElementById('goodInterval').textContent = formatInterval(this.currentScheduling['good'].card);
        }
        if (this.currentScheduling['easy'] && this.currentScheduling['easy'].card) {
            document.getElementById('easyInterval').textContent = formatInterval(this.currentScheduling['easy'].card);
        }
    }
    
    updateStats() {
        // Обновляем счетчики
        const now = new Date();
        let newCount = 0;
        let dueCount = 0;
        
        this.cards.forEach(card => {
            const state = this.cardStates[card.id];
            if (state.state === 0) { // 0 = New
                newCount++;
            } else if (state.due <= now) {
                dueCount++;
            }
        });
        
        const availableNew = Math.max(0, Math.min(newCount, this.settings.dailyNewLimit - this.todayStats.newCards));
        const availableReview = Math.max(0, Math.min(dueCount, this.settings.dailyReviewLimit - this.todayStats.reviewCards));
        
        document.getElementById('newCards').textContent = availableNew;
        document.getElementById('studiedToday').textContent = this.todayStats.studied;
        document.getElementById('remainingCards').textContent = availableNew + availableReview;
        
        if (this.todayStats.studied > 0) {
            const accuracy = Math.round((this.todayStats.correct / this.todayStats.studied) * 100);
            document.getElementById('accuracy').textContent = `${accuracy}%`;
        }
    }
    
    showCompletion() {
        document.getElementById('flashcard').style.display = 'none';
        document.getElementById('ratingButtons').style.display = 'none';
        document.getElementById('completion').style.display = 'block';
    }
    
    showError(message) {
        // Показать ошибку пользователю
        console.error(message);
        const cardContent = document.getElementById('cardContent');
        cardContent.textContent = `Ошибка: ${message}`;
        cardContent.className = 'card-content error';
    }
    
    initializeUI() {
        // Обработчики событий
        const flashcard = document.getElementById('flashcard');
        const playBtn = document.getElementById('playBtn');
        
        flashcard.addEventListener('click', (e) => {
            if (e.target.closest('.audio-btn')) return;
            this.showAnswer();
        });
        
        playBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.playAudio();
        });
        
        // Кнопки оценки
        document.querySelectorAll('.rating-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const rating = parseInt(btn.dataset.rating);
                this.rateCard(rating);
            });
        });
        
        // Переключение режимов
        document.querySelectorAll('.mode-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.studyMode = btn.dataset.mode;
                if (!this.isFlipped) {
                    this.showCard();
                }
            });
        });
        
        // Настройки
        document.getElementById('autoplaySetting').addEventListener('click', async () => {
            this.settings.autoplay = !this.settings.autoplay;
            document.getElementById('autoplayToggle').classList.toggle('active', this.settings.autoplay);
            await this.saveSettings();
        });
        
        document.getElementById('intervalsSetting').addEventListener('click', async () => {
            this.settings.showIntervals = !this.settings.showIntervals;
            document.getElementById('intervalsToggle').classList.toggle('active', this.settings.showIntervals);
            
            const intervals = document.querySelectorAll('.rating-interval');
            intervals.forEach(el => {
                el.style.display = this.settings.showIntervals ? 'block' : 'none';
            });
            
            if (this.settings.showIntervals) {
                this.updateIntervals();
            }
            
            await this.saveSettings();
        });
        
        // Продолжить после завершения
        document.getElementById('continueBtn').addEventListener('click', () => {
            // Сбрасываем лимиты для продолжения
            this.todayStats.newCards = 0;
            this.todayStats.reviewCards = 0;
            
            document.getElementById('completion').style.display = 'none';
            document.getElementById('flashcard').style.display = 'flex';
            this.selectNextCard();
        });
        
        // Клавиатурные сокращения
        document.addEventListener('keydown', (e) => {
            if (e.key === ' ') {
                e.preventDefault();
                if (!this.isFlipped) {
                    this.showAnswer();
                } else {
                    this.rateCard(3); // Good
                }
            } else if (e.key === 'p' || e.key === 'з') {
                this.playAudio();
            } else if (this.isFlipped && e.key >= '1' && e.key <= '4') {
                this.rateCard(parseInt(e.key));
            }
        });
        
        // Обновляем UI с текущими настройками
        document.getElementById('autoplayToggle').classList.toggle('active', this.settings.autoplay);
        document.getElementById('intervalsToggle').classList.toggle('active', this.settings.showIntervals);
        document.getElementById('totalCards').textContent = this.cards.length;
    }
    
    async saveSettings() {
        const { error } = await this.supabase
            .from('user_settings')
            .upsert({
                user_id: this.user.id,
                autoplay: this.settings.autoplay,
                show_intervals: this.settings.showIntervals,
                daily_new_limit: this.settings.dailyNewLimit,
                daily_review_limit: this.settings.dailyReviewLimit
            });
        
        if (error) {
            console.error('Error saving settings:', error);
        }
    }
}

// Экспорт для использования в основном файле
window.SpanishCardsApp = SpanishCardsApp;
