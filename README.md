# jetix-lampa-plugin

Плагин для [Lampa](https://github.com/yumata/lampa-source), который показывает текущую трансляцию из группы VK (в том числе закрытой) прямо в Lampa на телевизоре. В левом меню появляется пункт **Jetix**; нажал — и пошёл эфир.

```
Lampa (ТВ) ── плагин с GitHub Pages
     │
     └──► https://ваш-домен/<SECRET>/live.json, live.m3u8
              │  (существующий nginx / Caddy → 127.0.0.1:18731)
              ▼
         server/app.py ── VK API (video.get, wall.get) + yt-dlp ──► CDN VK
```

- **plugin/** — плагин. Публикуется на GitHub Pages и не содержит никаких секретов.
- **server/** — сервер на Python, только стандартная библиотека и `yt-dlp`. Слушает `127.0.0.1` и встаёт рядом с уже работающими сайтами за их веб-сервер, под путём `/<SECRET>/`.
- **deploy/** — установка, systemd, сниппеты для nginx и Caddy.

## Требования

- Сервер с Ubuntu 20.04+ и уже настроенным HTTPS-сайтом: nginx, Caddy или другой reverse proxy.
- Аккаунт VK, который состоит в группе.

## 1. Плагин на GitHub Pages

Settings → Pages → Source: **GitHub Actions**. Workflow `.github/workflows/pages.yml` публикует папку `plugin/` при каждом пуше в `main`. Плагин будет доступен по адресу:

```
https://<github-user>.github.io/jetix-lampa-plugin/jetix.js
```

## 2. Сервер

```bash
git clone https://github.com/<github-user>/jetix-lampa-plugin.git
cd jetix-lampa-plugin
sudo ./deploy/install.sh https://ваш-домен
```

Скрипт создаёт пользователя `jetix`, ставит код в `/opt/jetix` и yt-dlp в виртуальное окружение, а также генерирует `SECRET`. Сервис поднимается на `127.0.0.1:18731`. Ещё он включает ежедневное обновление yt-dlp. nginx и сертификаты скрипт не трогает.

Обновление: `git pull && sudo ./deploy/install.sh`. Файлы `.env` и `cookies.txt` при этом сохраняются.

## 3. Настройка `/opt/jetix/.env`

| Переменная | Значение |
|---|---|
| `VK_TOKEN` | пользовательский токен участника группы (см. ниже) |
| `GROUP_ID` | числовой ID группы без минуса |
| `ALBUM_ID` | необязательно: искать только в этом альбоме |
| `SEARCH_WALL` | `1` — искать трансляции и во вложениях постов |
| `COOKIES` | путь к `cookies.txt` (формат Netscape) для yt-dlp |
| `PORT` | локальный порт, по умолчанию `18731` |
| `SECRET` | секретная часть пути, генерируется при установке |
| `PUBLIC_URL` | `https://ваш-домен`, без слеша в конце |
| `PROXY_MODE` | `off`, `manifest` или `full` (см. «Если не играет») |
| `CHANNEL_NAME` | название канала в M3U |
| `VK_VIDEOS_URL` | режим без токена: `https://vk.com/video/@club<ID>`, нужны cookies |

После изменений: `sudo systemctl restart jetix`.

**Токен.** Нужен пользовательский токен с правами `video,wall,groups,offline`. Распространённый способ — implicit flow с `client_id` готового приложения. Откройте ссылку в браузере, где вы вошли в нужный аккаунт:

```
https://oauth.vk.com/authorize?client_id=2685278&scope=video,wall,groups,offline&redirect_uri=https://oauth.vk.com/blank.html&display=page&response_type=token&revoke=1
```

Затем скопируйте `access_token` из адресной строки. Токен даёт доступ к аккаунту: храните его только в `.env` на сервере. Отозвать токен можно в настройках VK, в разделе приложений и сеансов. Удобнее использовать отдельный аккаунт.

**GROUP_ID.** Это число из `vk.com/club123456`. Если у группы короткое имя, откройте любое видео группы: в адресе будет `video-123456_...`.

**Cookies.** Если API не отдаёт ссылку на поток, её получает yt-dlp, а для закрытой группы ему нужны cookies. Экспортируйте cookies vk.com расширением вроде *Get cookies.txt LOCALLY* и положите файл в `/opt/jetix/cookies.txt`. Затем выполните `chown jetix:jetix` и `chmod 600`.

**Проверка во время эфира:**

```bash
sudo -u jetix /opt/jetix/.venv/bin/python /opt/jetix/server/check.py
```

Скрипт печатает найденные трансляции и ссылку `.m3u8`. Её можно открыть в VLC. Ключи для отладки: `--raw` и `--all`.

## 4. Подключение к веб-серверу

nginx — добавьте содержимое `deploy/nginx-location.conf` внутрь `server { … }` существующего HTTPS-сайта, заменив `JETIX_SECRET` на ваш `SECRET`:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Caddy — используйте `deploy/Caddyfile.snippet` внутри блока сайта.

Проверка: `https://ваш-домен/<SECRET>/status`. Любой другой путь сервер не обслуживает и отвечает 404.

## 5. Lampa

Настройки → Расширения → Добавить плагин:

```
https://<github-user>.github.io/jetix-lampa-plugin/jetix.js?s=ваш-домен/<SECRET>
```

Параметр `s` с адресом сервера плагин запоминает при первом запуске. Адрес хранится только в Lampa на вашем устройстве и в публичный репозиторий не попадает. Поменять адрес и проверить подключение можно в Настройки → Jetix.

Как ведёт себя пункт меню:
- эфир идёт — открывается плеер;
- эфир закончился — включается запись;
- эфир запланирован — показывается время начала;
- красная точка на иконке — эфир сейчас идёт.

Запасной вариант: IPTV-плагин Lampa с плейлистом `https://ваш-домен/<SECRET>/playlist.m3u`.

## API сервера

Все адреса начинаются с `/<SECRET>/`. Чтобы сбросить кэш, добавьте `?fresh=1`.

| Путь | Ответ |
|---|---|
| `live.m3u8` | HLS текущего эфира |
| `record.mp4` | запись, если у завершённого эфира нет HLS |
| `live.json` | `{live, status, title, thumb, page, start_time, playable, type, url, error}` |
| `live.json?lite=1` | статус без запуска yt-dlp |
| `playlist.m3u` | M3U с одним каналом |
| `status` | диагностика текстом |
| `health` | `ok` |

Приоритет выбора трансляции: идущая → ближайшая запланированная → последняя запись. Список трансляций кэшируется на 60 с, ссылка на поток — на 90 с.

## Если не играет

- `VK API ошибка 5` — токен недействителен.
- `VK API ошибка 15/203/204` — аккаунт токена не состоит в группе.
- `yt-dlp: … log in / unavailable` — нужны свежие cookies.
- `security_check` — VK требует подтвердить вход с нового IP. Подтвердите вход в браузере и переэкспортируйте cookies.
- yt-dlp сломался после изменений VK — `sudo systemctl start jetix-ytdlp-update && sudo systemctl restart jetix`.
- В VLC поток играет, а в Lampa нет — меняйте `PROXY_MODE` по порядку:

| Режим | Через сервер | Трафик |
|---|---|---|
| `off` | ничего, 302 на CDN | нет |
| `manifest` | только `.m3u8` | минимальный |
| `full` | плейлисты и сегменты | весь поток, ~1–3 ГБ за 2 часа |

Прокси-ссылки подписаны HMAC от `SECRET`, поэтому открытым прокси сервер не становится.

## Разработка

```bash
python3 -m unittest discover -s tests -v
```

Тесты подменяют VK API и yt-dlp, а вместо CDN поднимают фейковый сервер. Плагин написан на ES5 для старых WebView телевизоров; CI проверяет это через `acorn --ecma5`.
