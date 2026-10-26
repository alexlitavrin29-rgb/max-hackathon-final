# Стенд качества ответов «Точки опоры»

Владелец — редактор. Критерий: коротко назвать конкретные документы/действия сразу,
подробности дать по подписанным внутренним карточкам и поискам. Ссылка на хорошую
карточку не исправляет пустое вступление. Этот стенд диагностирует приложение;
его установка сама по себе НЕ меняет ответы приложения.

## Наборы и происхождение результатов

- Старый frozen `../questions.v1.json`,74кейса, не изменять. Это регрессии, не live accuracy.
- `owner.v1.json`:15 owner/developmentкейсов. Шесть точных вопросов владельца,
  вопрос со скриншота и восемь разработческих вариантов. Это НЕ независимый holdout.
- `calibration.v1.json`: негативный текст со скриншота, технический отказ и
  короткий позитивный ориентир. Позитивный пример из опубликованной partialкарточки;
  не подтверждает актуальную редакцию закона. Оценка редактором ещё не выполнена.
- Offline подаёт заданные oracleID в настоящий build_information. Результат
  `oracle_assembly_NOT_model` не доказывает правильный выбор живой моделью.
  Для неоднозначности/вопроса вне базы без вызова модели стоит NOT_RUN.
- Срочная reviewedпамятка выполняется настоящим shortcut, отдельно от modelcredit.
- Ключевые фрагменты/известные вводные/длина проверяются кодом. Это механические
  признаки, не общее семантическое понимание. Нужны ручная калибровка и judge.

## Запуск без платных вызовов

Из корня source с проектными Python зависимостями:

```sh
.venv/bin/python tools/submission/answer_eval_stand.py --output-dir /tmp/answer-stand
npm ci --prefix submission/ai-evals/stand
```

Promptfoo зафиксирован0.124.1 в отдельном package/lock, production package не меняется.
Для CLI задать локальные каталоги и отключить телеметрию/update:

```sh
PROMPTFOO_CONFIG_DIR=/tmp/tochka-promptfoo-state PROMPTFOO_DISABLE_TELEMETRY=1 PROMPTFOO_DISABLE_UPDATE=1 PROMPTFOO_PYTHON="$PWD/.venv/bin/python" submission/ai-evals/stand/node_modules/.bin/promptfoo eval -c /tmp/answer-stand/promptfooconfig.json --no-cache --output /tmp/answer-stand/promptfoo.json
```

Exit100 означает провал проверок качества, а не поломку адаптера. NOT_RUN в
replay не получает PASS; это отдельно отмечено в snapshot.summary. Promptfoo
НЕ выполняет ни paid provider, ни judge — только воспроизводит готовые snapshots.
Поэтому параллелизм/повтор CLI не обходят бюджетный ledger.

Для сравнения с исторической базой восстановить её отдельным checkout и выполнить
в новом процессе ту же команду с `--source-root /path/to/baseline`; затем candidate
с `--baseline /path/to/baseline-output/snapshot.json`. Продуктовые импорты и публикация
берутся из указанной базы; корпус/критерии те же. Сгенерированный config содержит
локальные абсолютные пути: после переноса/clone сгенерировать снова.

## Просмотр и оценка редактором

Открыть `review.html` или запустить локальный сервер без публикации:

```sh
python -m http.server 8784 --bind 127.0.0.1 --directory /tmp/answer-stand
```

В браузере открыть http://127.0.0.1:8784/review.html. Есть обе темы, сравнение,
ссылки/полные материалы, Полезно/Вода/Ошибка/Неясно и комментарий. Оценки хранятся
только в этом браузере; «Скачать оценки редактора» выгружает JSON с fingerprint
точного snapshot. Это ручная калибровка, не автозасчитанная приёмка.
Внутренние ссылки показаны как mini-app маршруты, из локального отчёта в аккаунт
не входят. Внешние ссылки можно открыть отдельно. SafeURL/targetconcordance не
подтверждают HTTP-доступность или актуальность законодательства.

## Будущий платный пилот: <=50RUB на один общий ledger

В этой облачной среде configuredprovider/разрешённого доступа к нему нет. LiveNOT_RUN.
Не запрашивать/печатать ключи; использовать существующее локальное окружение.
Подтверждённый для расчёта тариф19.10.2026: ProxyAPI deepseek/deepseek-v4-flash,
20RUB/1Minput,40RUB/1Moutput, ссылка в eval_budget.PRICING. Другие provider/model
блокируются, пока для них не реализован отдельный проверенный расчёт.

Сначала выполнить offline: cost_plan показывает реальный размер productionprompts
без сети и отдельно ориентир judge по oracleкарточкам. Это НЕ фактический счёт.
Перед будущим запуском повторно сверить тариф, endpoint/model и квоты провайдера.

```sh
ANSWER_QUALITY_ALLOW_PROVIDER=1 .venv/bin/python tools/submission/answer_eval_stand.py --live --judge --limit 15 --ledger /path/to/one-pilot-budget.json --output-dir /tmp/live-answer-stand
```

Один ledger использовать для ВСЕХ повторов, ответов и judge этого пилота. Не удалять,
не менять путь, не обнулять. Persistentатомарные резервы перед вызовом: UTF8bytes
как консервативная верхняя оценка input +4096 framing, output600/1200 и ДВЕ попытки
транспорта. Резерв не возвращается даже при timeout/неизвестномusage;50RUB — потолок
суммы резервов при указанном тарифе, не обещание точного биллинга провайдера.
При повреждении/несовпадении тарифов журнал блокируется. Процесс последовательный,
15кейсов максимум, общий admissionPlannerAI сохраняется. MAX/VPS не вызываются.

Judge здесь отдельный вызов той же дешёвой модели: статус UNCALIBRATED_same_model,
это НЕ независимый эксперт. Проверяет concrete/relevant/conditions_preserved/brief,
даёт причину и точную цитату либо пустую строку для отсутствующего. Валидированные
вердикты, NOT_RUN, errors и mechanicalpass имеют отдельные знаменатели.
HTML показывает judge отдельно; при наличии verdicts генерируется отдельный
promptfoo-judge.json для диагностического сравнения, НЕ releasegate.
Ошибка judge не уничтожает ответ. Перед доверием к оценкам сверить10–15примеров
с редактором; спорные/правовые ответы подтверждает редактор по актуальным источникам.

## Проверки разработки

```sh
.venv/bin/python -m pytest project/admin/tests/test_answer_eval_stand.py project/admin/tests/test_answer_quality_eval.py -q
```

Mocktests проверяют бюджет/ошибки/адаптер, а не реальную нейросеть. Продуктовый код,
БД, текущие планы, напоминания и публикация бота в этой доработке не меняются.

Browser reproduction (candidate generated with --baseline):

```sh
node tools/submission/eval_stand_browser.cjs /tmp/answer-stand
```

Exports editor-test-* contain synthetic test input, NOT owner review.

## Короткие эталоны по отзыву владельца

`reference-replies.v1.json`:7коротких редакционных ориентиров по ownerвопросам,
обычно3–4пункта и подписанные material/searchlinks. Статус EDITOR_REVIEW_PENDING.
Это отдельные примеры, не подмена snapshot реальных ответов и не новая модельная
точность. В calibration.v1.json сокращён только позитивныйориентир; негативные
скриншотные тексты сохранены дословно. Frozen74 и его ожидания не менялись.

## Ответы после исправлений и проверка v2

Исторический `owner.v1.json` сохранён. В нём dev05 всегда помечен content_gap,
потому что прежний `job_contract` не отвечал на невыплату зарплаты. Этот набор
должен оставаться отдельным историческим измерением, а не получать молчаливую правку.

`owner.v2.json` меняет только этот сценарий: требуется новая явно добавленная
mini-app памятка `work_wages`, конкретные действия и обе официальные ссылки Роструда.
Запрещены опасный совет приостановить работу без условий и обещание выплаты.
Остальные вопросы/ожидания/лимиты длины те же. Source review не заменяет одобрение редактора.

```sh
.venv/bin/python tools/submission/answer_eval_stand.py --production --output-dir /tmp/owner-v1
.venv/bin/python tools/submission/answer_eval_stand.py --production --corpus submission/ai-evals/stand/owner.v2.json --output-dir /tmp/owner-v2
```

`--production` использует настоящий enrich после заданного oracle-выбора карточек.
Без флага сохраняется прежний build_information-only путь для воспроизведения старых
результатов. Ни один из них не измеряет правильность выбора живой моделью.
13/13 v2 и 12/13 v1 нельзя представлять как рост живой точности или сравнивать
проценты двух разных наборов ожиданий без объяснения изменения dev05.
Два остальных сценария (неоднозначный вопрос/вне базы) остаются NOT_RUN без модели.
