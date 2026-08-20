# Influencer Skill Lab

Учебный репозиторий для практического занятия Onegroup Digital: установить готовый навык из GitHub, проверить шесть синтетических блогеров, разобрать устройство `SKILL.md`, а затем собрать второй навык, который превращает результаты проверки в прозрачный шорт-лист.

Все профили, метрики, расшифровки и цены в репозитории вымышлены. Репозиторий не содержит клиентских данных, токенов и скрытых интеграций.

## Что внутри

- `skills/proverka-blogerov` — готовый навык с воспроизводимым деморежимом;
- `skills/shortlist-blogerov` — эталон второго навыка, который участники собирают по аналогии;
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

Скрипты используют только стандартную библиотеку Python 3.10+ и одинаково работают на macOS, Windows и Linux.

## Установка через Codex

Попросите Codex с помощью `$skill-installer` установить навык по фиксированной ссылке `https://github.com/neiro-viktor/influencer-skill-lab/tree/v1.0.1-classroom/skills/proverka-blogerov`. После установки перезапустите Codex, затем вызовите `$proverka-blogerov`.

Подробный маршрут находится в [workshop/participant-guide.md](workshop/participant-guide.md).
