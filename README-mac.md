# Сборка под macOS

1. Положи в репозиторий: все .py файлы, `requirements.txt`, `FotoSito-mac.spec`,
   `.github/workflows/build-mac.yml`, папку `assets`.
2. Не клади `settings_dialog-1.1.py` (старая копия).
3. Залей на GitHub. Вкладка Actions -> Build macOS -> Run workflow.
4. Скачай артефакт (zip) внизу страницы запуска. arm64 для M1/M2/M3, x86_64 для Intel.
5. Распакуй. Первый запуск: правый клик по приложению -> Открыть.
   Если пишет "повреждено": `xattr -cr FotoSito.app`.
