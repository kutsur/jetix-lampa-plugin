#!/usr/bin/env python3
import argparse
import json
import sys

import config
import vk


def main():
    ap = argparse.ArgumentParser(description="Найти трансляцию в группе и напечатать ссылку на поток")
    ap.add_argument("--raw", action="store_true", help="показать сырые поля первых видео из API")
    ap.add_argument("--all", action="store_true", help="получить поток для каждой найденной трансляции")
    args = ap.parse_args()

    problems = config.problems(require_secret=False)
    if problems:
        print("Проблемы конфигурации:")
        for p in problems:
            print("  -", p)
        return 2

    mode = "VK API (токен)" if config.VK_TOKEN else "yt-dlp по странице (без токена)"
    if config.cookies_path():
        cookies = config.cookies_path()
    elif config.COOKIES:
        cookies = "файл %s не найден — работаю без cookies" % config.COOKIES
    else:
        cookies = "нет"
    print("Группа: %s   режим: %s   cookies: %s" % (config.GROUP_ID or "-", mode, cookies))

    if args.raw and config.VK_TOKEN:
        resp = vk.api("video.get", owner_id=-config.GROUP_ID, count=5)
        for item in resp.get("items", []):
            item = dict(item)
            if item.get("files"):
                item["files"] = {k: (v[:60] + "…") for k, v in item["files"].items() if isinstance(v, str)}
            for k in ("access_key", "track_code"):
                if k in item:
                    item[k] = "…"
            print(json.dumps(item, ensure_ascii=False, indent=1)[:3000])
            print("-" * 60)

    try:
        streams, chosen, warnings = vk.find_streams()
    except vk.VKError as e:
        print("ОШИБКА:", e)
        return 1
    for w in warnings:
        print("Внимание:", w)

    if not streams:
        print("Трансляций не найдено. Проверьте GROUP_ID и что эфир лежит в разделе «Видео» группы или в постах.")
        return 1

    print("\nНайденные трансляции:")
    for s in streams:
        mark = "→" if chosen and s["key"] == chosen["key"] else " "
        print(" %s [%-8s] %-24s %s  (%s)" % (mark, s["status"], s["key"], s["title"], s["source"]))

    ok = False
    for s in (streams if args.all else [chosen]):
        if s["status"] == "upcoming":
            print("\n%s ещё не начался — ссылки на поток пока нет." % s["key"])
            continue
        print("\nПолучаю поток для %s …" % s["key"])
        try:
            media = vk.resolve_media(s)
        except vk.MediaError as e:
            print("  НЕ ПОЛУЧИЛОСЬ:")
            for part in str(e).split("; "):
                print("   -", part)
            continue
        ok = True
        print("  способ: %s, тип: %s" % (media["method"], media["type"]))
        if media.get("headers"):
            print("  заголовки от yt-dlp: %s" % ", ".join(sorted(media["headers"])))
        print("\n  " + media["url"] + "\n")
        print("  Откройте ссылку в VLC. Играет — подойдёт PROXY_MODE=off.")
        print("  Не играет, хотя ссылка есть — попробуйте PROXY_MODE=full.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
