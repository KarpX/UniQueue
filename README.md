# 🎓 UniQueue Bot — Умные очереди для студентов
## [![Start Bot](https://img.shields.io/badge/Telegram-Запустить%20бота-blue?logo=telegram)](https://t.me/UniQueueBot)

---

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)
![Aiogram](https://img.shields.io/badge/Aiogram-3.x-green)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-red)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Database-blue?logo=postgresql)
![Redis](https://img.shields.io/badge/Redis-FSM-red?logo=redis)
![Docker](https://img.shields.io/badge/Docker-Containerization-blue?logo=docker)
![GitHub Actions](https://img.shields.io/badge/CI/CD-GitHub%20Actions-black?logo=githubactions)

---
## 📌 О проекте

**UniQueue Bot** — это инструмент для автоматизации учебных очередей в университете. Он решает проблему хаоса в чатах учебных групп, заменяя бесконечные текстовые списки интерактивными сообщениями с «живым» обновлением.

### 🌟 Основной функционал:

- 🏫 **Система комнат:** Создание отдельных пространств для разных учебных групп или потоков.
- 📋 **Живые очереди:** Автоматическое обновление списка студентов в закрепленном сообщении группы.
- 🔗 **Deep Linking:** Мгновенный вход в комнату или переход к обмену местами по специальным ссылкам.
- 🤝 **Система Swap:** Возможность предложить другому студенту поменяться местами с подтверждением через личные сообщения.
- ⏭ **Функция пропуска:** Быстрый пропуск человека, стоящего следом за вами.
- 🔔 **Умные уведомления:** Бот пишет в личку, когда вы становитесь первым или вторым в очереди.
- 🛠 **Админ-панель:** Управление участниками (кик, назначение админов), очистка, переименование и удаление очередей.
- 🔄 **Persistence:** Благодаря Redis, ваши состояния (ввод названия, подтверждения) сохраняются даже после перезагрузки бота.

---

## ⚙️ Стек технологий

- **Python 3.11+**
- **Aiogram 3** — асинхронная работа с Telegram Bot API.
- **SQLAlchemy 2.0 (Async)** — современная ORM для работы с БД.
- **PostgreSQL** — надежное хранение данных.
- **Redis** — хранилище состояний (FSM) и кэша.
- **Alembic** — миграции базы данных.
- **Docker & Docker Compose** — контейнеризация и быстрое развертывание.
- **GitHub Actions** — автоматический деплой на сервер при пуше в main.

---

## 🚀 Быстрый старт

### 1. Клонировать проект

```bash
git clone https://github.com/KarpX/UniQueue.git
cd UniQueue
```
### 2. Создать .env файл
Создайте файл .env в корневой папке и заполните его:
```env
BOT_TOKEN=ваш_токен_от_botfather
DATABASE_USER=karpx
DATABASE_PASSWORD=ваш_пароль
DATABASE_NAME=uniqueue_db
DATABASE_HOST=db
DATABASE_PORT=5432
REDIS_HOST=redis
```
### 3. Запуск через Docker
Система автоматически поднимет контейнеры с ботом, базой данных PostgreSQL и Redis:
```bash
docker compose up -d --build
```
### 4. Применить миграции
```bash
docker compose exec bot alembic upgrade head
```
### 🛠 Команды в группах
- /bind [код] — привязать текущую группу к комнате (только для админов).
- /queue — вызвать интерактивный список очередей.
- /help — получить подробную инструкцию по работе с ботом.
### 👤 Авторы
KarpX – ведущий разработчик:
[![Telegram](https://img.shields.io/badge/Contact-KarpX-blue?logo=telegram)](https://t.me/KarpXer)
### ⭐️ Поддержка
Если этот бот помог вашей учебной группе стать организованнее — поставь ⭐️ на GitHub!

Developed with ❤️ for students.
