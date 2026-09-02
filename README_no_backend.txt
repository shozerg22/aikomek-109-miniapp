Telegram Mini App без backend API

1. Загрузите smart_aqmola_109_index_senddata.html на хостинг и переименуйте в index.html.
2. Получите публичную HTTPS-ссылку, например https://...vercel.app
3. В файле bot_no_backend.py замените:
   - ВСТАВЬ_ТОКЕН_ОТ_BOTFATHER на токен бота
   - ВСТАВЬ_ССЫЛКУ_НА_MINI_APP на HTTPS-ссылку Mini App
4. Запустите start_bot_windows.bat
5. В Telegram напишите боту /start
6. Нажмите кнопку «Подать обращение 109».

Важно:
- В этом режиме данные формы уходят в Telegram-бот через Telegram.WebApp.sendData.
- Это работает без отдельного backend API.
- Фото как файл в бот не передается, передается только количество выбранных фото.
- Для полноценной передачи фото и работы с CRM позже нужен backend/API.
- Заявки сохраняются локально на компьютере в папке data: appeals.csv и appeals.jsonl.
