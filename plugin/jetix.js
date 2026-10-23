(function () {
    'use strict';

    if (window.jetix_plugin_loaded) return;
    window.jetix_plugin_loaded = true;

    var NAME = 'Jetix';
    var ACTION = 'jetix';
    var STORAGE_KEY = 'jetix_server';
    var POLL_MS = 5 * 60 * 1000;

    var ICON =
        '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" fill="none">' +
        '<rect x="2" y="5" width="20" height="14" rx="3" stroke="currentColor" stroke-width="2"/>' +
        '<path d="M10 9.2v5.6c0 .4.4.6.7.4l4.4-2.8c.3-.2.3-.6 0-.8l-4.4-2.8c-.3-.2-.7 0-.7.4z" fill="currentColor"/>' +
        '<circle class="jetix-dot" cx="19" cy="5" r="3" fill="#e53935" style="display:none"/>' +
        '</svg>';

    var SELF_SRC = (function () {
        if (document.currentScript && document.currentScript.src) return document.currentScript.src;
        var scripts = document.getElementsByTagName('script');
        for (var i = scripts.length - 1; i >= 0; i--) {
            if (/jetix[^\/]*\.js/.test(scripts[i].src || '')) return scripts[i].src;
        }
        return '';
    })();

    var busy = false;

    function queryParam(url, name) {
        var m = new RegExp('[?&]' + name + '=([^&#]*)').exec(url || '');
        if (!m) return '';
        try {
            return decodeURIComponent(m[1].replace(/\+/g, ' '));
        } catch (e) {
            return m[1];
        }
    }

    function normalizeServer(value) {
        var v = String(value || '').replace(/^\s+|\s+$/g, '');
        if (!v) return '';
        if (!/^https?:\/\//i.test(v)) v = 'https://' + v;
        v = v.replace(/\/(live\.json|live\.m3u8|playlist\.m3u|status)(\?.*)?$/, '');
        return v.replace(/\/+$/, '');
    }

    function server() {
        return normalizeServer(Lampa.Storage.get(STORAGE_KEY, ''));
    }

    function noty(text) {
        if (window.Lampa && Lampa.Noty) Lampa.Noty.show(text);
    }

    function pad(n) {
        return n < 10 ? '0' + n : '' + n;
    }

    function formatTime(unix) {
        if (!unix) return '';
        var d = new Date(unix * 1000);
        var time = pad(d.getHours()) + ':' + pad(d.getMinutes());
        if (d.toDateString() === new Date().toDateString()) return 'сегодня в ' + time;
        return pad(d.getDate()) + '.' + pad(d.getMonth() + 1) + ' в ' + time;
    }

    function getJSON(url, timeout, done, fail) {
        var xhr = new XMLHttpRequest();
        var finished = false;
        xhr.open('GET', url + (url.indexOf('?') === -1 ? '?' : '&') + 't=' + Date.now(), true);
        xhr.timeout = timeout;
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== 4 || finished) return;
            finished = true;
            if (xhr.status >= 200 && xhr.status < 300) {
                var data;
                try {
                    data = JSON.parse(xhr.responseText);
                } catch (e) {
                    return fail('сервер вернул не JSON — проверьте адрес');
                }
                done(data);
            } else if (xhr.status === 404) {
                fail('адрес сервера неверный (404)');
            } else {
                fail(xhr.status ? 'сервер ответил ' + xhr.status : 'сервер недоступен');
            }
        };
        xhr.ontimeout = function () {
            if (finished) return;
            finished = true;
            fail('сервер не ответил вовремя');
        };
        xhr.send();
    }

    function shortError(err) {
        var s = String(err || '').split('; ')[0];
        return s.length > 140 ? s.substring(0, 140) + '…' : s;
    }

    function play(data) {
        var retried = false;
        var item = {
            title: data.title || NAME,
            url: data.url,
            need_check_live_stream: data.status === 'live',
            error: function (work, callback) {
                if (retried) return;
                retried = true;
                noty('Переподключаюсь к эфиру…');
                callback(data.url + (data.url.indexOf('?') === -1 ? '?' : '&') + 'fresh=1&t=' + Date.now());
            }
        };
        if (Lampa.Player.playlist) Lampa.Player.playlist([{ title: item.title, url: item.url }]);
        Lampa.Player.play(item);
    }

    function open() {
        var base = server();
        if (!base) return noty('Укажите адрес сервера: Настройки → ' + NAME);
        if (busy) return;
        busy = true;
        noty('Ищу эфир…');
        getJSON(base + '/live.json', 60000, function (data) {
            busy = false;
            setDot(data.live);
            if (data.playable && data.url) {
                if (data.status !== 'live') noty('Эфир завершён — включаю запись');
                play(data);
            } else if (data.status === 'upcoming') {
                var when = formatTime(data.start_time);
                noty('Эфир ещё не начался' + (when ? ': ' + when : '') + (data.title ? ' — ' + data.title : ''));
            } else if (data.error) {
                noty(NAME + ': ' + shortError(data.error));
            } else {
                noty('Сегодня эфира пока нет');
            }
        }, function (err) {
            busy = false;
            noty(NAME + ': ' + err);
        });
    }

    function setDot(on) {
        var dot = $('.menu__item[data-action="' + ACTION + '"] .jetix-dot');
        if (dot.length) dot.css('display', on ? '' : 'none');
    }

    function poll() {
        var base = server();
        if (!base) return setDot(false);
        getJSON(base + '/live.json?lite=1', 20000, function (data) {
            setDot(data.live);
        }, function () {
            setDot(false);
        });
    }

    function testConnection() {
        var base = server();
        if (!base) return noty('Адрес сервера не указан');
        noty('Проверяю…');
        getJSON(base + '/live.json?lite=1', 20000, function (data) {
            var states = { live: 'эфир идёт', upcoming: 'эфир запланирован', finished: 'есть запись', none: 'трансляций нет' };
            noty(data.error ? 'Сервер отвечает, но: ' + shortError(data.error) : 'Подключено: ' + (states[data.status] || data.status));
        }, function (err) {
            noty('Ошибка: ' + err);
        });
    }

    function addSettings() {
        if (!Lampa.SettingsApi) return;
        Lampa.SettingsApi.addComponent({ component: ACTION, name: NAME, icon: ICON });
        Lampa.SettingsApi.addParam({
            component: ACTION,
            param: { name: STORAGE_KEY, type: 'input', values: '', default: '', placeholder: 'https://example.com/секрет' },
            field: { name: 'Адрес сервера', description: 'Адрес со страницы status сервера' },
            onChange: poll
        });
        Lampa.SettingsApi.addParam({
            component: ACTION,
            param: { name: 'jetix_test', type: 'button' },
            field: { name: 'Проверить подключение' },
            onChange: testConnection
        });
    }

    function addMenuItem() {
        var menu = Lampa.Menu.render();
        if (menu.find('[data-action="' + ACTION + '"]').length) return;
        var item = $(
            '<li class="menu__item selector" data-action="' + ACTION + '">' +
            '<div class="menu__ico">' + ICON + '</div>' +
            '<div class="menu__text">' + NAME + '</div>' +
            '</li>'
        );
        item.on('hover:enter', open);
        var anchor = menu.find('[data-action="tv"]');
        if (anchor.length) anchor.after(item);
        else if (Lampa.Menu.addElement) Lampa.Menu.addElement(item);
        else menu.find('.menu__list').eq(0).prepend(item);
    }

    function start() {
        var fromUrl = normalizeServer(queryParam(SELF_SRC, 's'));
        if (fromUrl && fromUrl !== server()) Lampa.Storage.set(STORAGE_KEY, fromUrl);
        try {
            addSettings();
        } catch (e) {
            console.log('[Jetix] settings', e);
        }
        try {
            addMenuItem();
        } catch (e) {
            console.log('[Jetix] menu', e);
        }
        poll();
        setInterval(poll, POLL_MS);
    }

    if (window.appready) start();
    else {
        Lampa.Listener.follow('app', function (e) {
            if (e.type === 'ready') start();
        });
    }
})();
