# git-private2public

**[English](./README.md)** · **[Русский](./README.ru.md)**

---

**Как `.gitignore`, только для публичности.**

У тебя приватный репо. Нужен публичный — без секретов. Эта тулза держит их в
синке. Автоматически.

## Быстрый старт

```bash
pip install git-filter-repo pyyaml
git-private2public init          # создаёт папку .gitpublic/
```

Отредактируй `.gitpublic/config` — source и target:

```
source = you/private-repo
target = you/public-repo
```

Отредактируй `.gitpublic/ignore` — что прятать, по строке (как `.gitignore`):

```
.env
secrets/
*.key
```

Опубликуй:

```bash
git-private2public publish
```

Готово. Публичный репо чистый.

## Авто-публикация при каждом `git push`

```bash
git-private2public hook enable     # вкл
git push                           # также публикует публичный mirror
git-private2public hook disable    # выкл
```

Нативный git-хук. Без CI, без GitHub Actions. Работает офлайн.

## Папка `.gitpublic/`

Каждый файл — одна забота. Как `.gitignore` — одно правило на строку, `#` для
комментариев. Если файла нет — настройки просто нет.

| Файл | Что внутри | Формат |
|------|------------|--------|
| `config` | source, target, push | `key = value` |
| `ignore` | что НЕ публиковать | путь/маска на строку |
| `replace` | найти → заменить в файлах | `old ==> new` на строку |
| `scan` | отказаться пушить если найдёт | паттерн на строку |
| `allow` | домены которые ОК | по одному на строку |

**Простой** — редактируй только `ignore`:

```
.env
secrets/
*.key
```

**Средний** — ещё `replace`:

```
10.0.0.5 ==> 203.0.113.5
real-token ==> ***
regex:[A-Fa-f0-9]{64} ==> ***
```

**Сложный** — ещё `scan` + `allow`:

```
# scan:
regex:github_pat_[A-Za-z0-9_]{30,}
regex:192\.168\.

# allow:
get.docker.com
```

## Команды

```
init        создать конфиг
scan        проверить, не пушить
publish     вычистить + запушить
hook        enable / disable / status
```

## Установка

```bash
pip install git-private2public
```

Готово. Теперь есть команда `git-private2public`.

> Без pip? [Ручная установка одного файла](./git_private2public.py) — скачать +
> `chmod +x` (нужно `pip install git-filter-repo pyyaml`).

## Зачем

В Git нет «приватного файла в публичном репо». Поэтому нужно два репо. Эта
тулза держит их в синке — без утечек.

| | удалить файлы | заменить текст | скан | авто пуш |
|---|:---:|:---:|:---:|:---:|
| git-filter-repo | ✅ | ✅ | ❌ | ❌ |
| BFG | ✅ | ✅ | ❌ | ❌ |
| dupligit | ❌ | ❌ | ❌ | ✅ |
| **git-private2public** | ✅ | ✅ | ✅ | ✅ |

## Лицензия

MIT
