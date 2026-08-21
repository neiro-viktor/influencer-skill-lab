# Influencer Skill Lab

Учебный репозиторий для практического занятия Onegroup Digital: установить готовый навык из GitHub, проверить шесть синтетических блогеров, разобрать устройство `SKILL.md`, а затем собрать второй навык, который превращает результаты проверки в прозрачный шорт-лист.

Все профили, метрики, расшифровки и цены в репозитории вымышлены. Репозиторий не содержит клиентских данных, токенов и скрытых интеграций.

## Что внутри

- `skills/proverka-blogerov` — готовый навык с воспроизводимым деморежимом;
- `skills/shortlist-blogerov` — эталон второго навыка, который участники собирают по аналогии;
- `pilot-register.csv` — контракт передачи результата: версия, владелец, baseline, контур, зависимости и повторный замер;
- `workshop` — сценарий занятия, промпты, ожидаемые результаты и план «Б»;
- `tests` — проверки расчётов и контрактов выходных файлов.

## Быстрый запуск без установки навыка

```bash
python3 skills/proverka-blogerov/scripts/demo_screening.py --output output/screening
python3 skills/shortlist-blogerov/scripts/build_shortlist.py \
  --screening output/screening/screening.csv \
  --brief skills/shortlist-blogerov/assets/campaign-brief.json \
  --output output/shortlist
python3 -m unittest discover -s tests -v
```

Учебный прогон заканчивается командой:

```bash
python3 skills/shortlist-blogerov/scripts/verify_handoff.py \
  --pilot output/shortlist/pilot-register.csv --allow-classroom
```

Перед рабочим запуском заменить учебный baseline и владельца фактическими данными, затем получить строгий PASS без `--allow-classroom`.

Скрипты используют только стандартную библиотеку Python 3.10+ и одинаково работают на macOS, Windows и Linux.

## Установка через Codex

Попросите Codex с помощью `$skill-installer` установить навык по фиксированной ссылке `https://github.com/neiro-viktor/influencer-skill-lab/tree/v1.0.3-classroom/skills/proverka-blogerov`. После установки начните новый запрос и вызовите `$proverka-blogerov`. Codex обнаруживает новые навыки автоматически; перезапуск нужен только как запасной шаг, если навык не появился при вводе `$` или в `/skills`.

Подробный маршрут находится в [workshop/participant-guide.md](workshop/participant-guide.md).
