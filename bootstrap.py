"""Register this real project and its agreed roadmap. Never resets existing records."""
from pathlib import Path
import sys
from core import Store, ROOT


def bootstrap():
    store = Store()
    existing = [p for p in store.projects() if Path(p['path']).resolve() == ROOT]
    if existing:
        print(existing[0]['id'])
        return
    project = store.create_project('Project Map · наша разработка', str(ROOT))['id']
    goal = store.create_node(project, 'goal', 'Карта проекта и оркестратор в одном месте',
        body='Из нашего разговора: сохранять ход работы, уточнения, цели, спецификации, задачи, исправления и реальные проверки. Подключать разные проекты. Экономить контекст, не теряя историю.')
    request = store.create_node(project, 'request', 'Все задачи и исправления появляются в карте автоматически',
        body='Уточнение пользователя: задачи и исправления должны автоматически создаваться в карте. В первой версии это гарантируется для записей через Project Map; перехват всех чатов — отдельная задача.', parent=goal['id'])
    spec = store.create_node(project, 'spec', 'Спецификация и условия приёмки', body=(ROOT/'SPEC.md').read_text(encoding='utf-8'), parent=goal['id'])
    base = store.create_node(project, 'task', 'Локальная карта, история и сохранение состояния',
        body='Рабочий интерфейс, SQLite, атомарные события, поиск, документы и экспорт.', parent=goal['id'],
        criteria='Перезапуск сохраняет записи. Конкурентная правка не теряет чужой текст. Новая задача и исправление видны на карте.', scope='core.py, web/')
    store.create_node(project, 'fix', 'Закрывать SQLite-соединения после каждой операции',
        body='Обнаружено первым прогоном: на Windows открытые подключения мешали освобождать временную базу. Добавлено явное закрытие соединений.',
        parent=base['id'], criteria='Все тесты завершаются без ошибки блокировки SQLite-файла.', scope='core.py')
    mcp = store.create_node(project, 'task', 'Подключить карту к Codex через MCP',
        body='Сохранение задач, исправлений и кратких решений из чата. Проверка stdio-протокола и регистрации инструмента.', parent=request['id'],
        criteria='MCP создаёт исправление; интерфейс показывает его после обновления. Подключение в новом чате требует проверки.', scope='app.py')
    store.create_node(project, 'task', 'Подписка на события существующих чатов Codex',
        body='Проверить поддерживаемые hooks и App Server. Не читать внутренние базы клиента без обоснования. Доказать отсутствие пропусков и дублей, прежде чем обещать автоматический сбор всех чатов.',
        parent=request['id'], criteria='Реальное сообщение, задача и исправление отображаются без ручного вызова инструмента. Повтор события не дублирует запись.', scope='integration')
    orchestration = store.create_node(project, 'task', 'Параллельные исполнители и восстановление работы',
        body='Отдельные рабочие копии, ограничение числа исполнителей, распределение файлов, зависимости, отмена и восстановление. Контроль расхода по реальным данным.',
        parent=goal['id'], criteria='Два независимых исполнителя завершают задачи без пересечений; прерванный запуск восстанавливается; объединённый результат проверяется.', scope='orchestrator')
    store.depend(orchestration['id'], mcp['id'])
    store.create_node(project, 'task', 'PR, CI, staging и защита основной ветки',
        body='После выбора удалённого репозитория и среды. Нужны проверка конкретной версии, приёмка и отдельное разрешение на публикацию.', parent=goal['id'],
        criteria='Провал обязательной проверки блокирует интеграцию; выпуск относится к проверенной версии.', scope='delivery')
    store.create_node(project, 'task', 'Поиск по знаниям и карта связей кода',
        body='Сначала оценить полезность текстового поиска. Затем добавить RAG и code graph с указанием источников и актуальности.', parent=goal['id'],
        criteria='Результат поиска указывает реальный источник, изменение файла инвалидирует устаревшие данные.', scope='retrieval')
    store.create_node(project, 'question', 'Где размещать общий репозиторий и испытательную среду?',
        body='Нужны выбор владельца/репозитория и среды перед настройкой PR, CI и staging. Локальная работа от ответа не зависит.', parent=goal['id'])
    store.create_node(project, 'decision', 'Отдельный проект, локальное хранение, постепенное подключение',
        body='Согласовано: создаём отдельную папку Codex-Project-Map. Исходный Jarvis_X_Altron не меняем. Изображения служат ориентиром, их обещания экономии не считаются измерениями.', parent=goal['id'])
    store.set_runner(project, [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
    print(project)


if __name__ == '__main__':
    bootstrap()
