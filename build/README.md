# Windows build

Актуальная тестовая Windows-сборка публикуется в GitHub Releases под тегом `dev-latest`.

В архиве находятся:

- **GIS JKH Assistant.exe** — настольная программа;
- папка `browser-extension` — расширение **ГИС ЖКХ помощник**.
- `README.md`, `CHANGELOG.md`, `VERSION.txt` и `BUILD_INFO.json` — инструкции, версия, commit и SHA256 исполняемого файла.

Файлы помощника также встроены в EXE и Python-пакет. В **Настройках** программы доступен мастер подготовки
постоянной папки и ручного добавления расширения в Chrome, Edge или Яндекс Браузере.
После изменения `browser-extension/manifest.json` или `background.js` обновите встроенный ресурс командой
`python -B tools/package_browser_helper.py`. Проверка `--check` и тесты обнаруживают устаревший ресурс.

Страница актуальной тестовой сборки:

https://github.com/serbo26-lab/gis-jkh-assistant/releases/tag/dev-latest

После успешной сборки ветки `main` архив в `dev-latest` заменяется новой версией.
