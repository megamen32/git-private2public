# git-private2public

**[English](./README.md)** · **[Русский](./README.ru.md)**

---

**Как `.gitignore`, только для публичности.**

Нужно полное объяснение всех правил? См. [Advanced configuration](./docs/ADVANCED.md) / [RU](./docs/ADVANCED.ru.md).

У тебя приватный репо. Нужен публичный — без секретов. Эта тулза держит их в
синке. Автоматически.

## Быстрый старт

```bash
pip install git-private2public
git-private2public init          # создаёт папку .gitpublic/
```

Отредактируй `.gitpublic/config` — source и target. Значения могут быть `owner/repo`, полный Git URL или локальный путь:

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
| `allow` | домены, которые ОК при совпадении `scan` рядом | по одному домену в строке |

**Простой** — редактируй только `ignore`:

```
.env
secrets/
*.key
```

**Средний** — ещё `replace`:

```
<PRIVATE_IP> ==> 203.0.113.5
real-token ==> ***
regex:[A-Fa-f0-9]{64} ==> ***
```

**Сложный** — ещё `scan` + `allow`:

```
# scan:
regex:github_pat_[A-Za-z0-9_]{30,}
regex:192\.168\.
regex:[a-z0-9.-]+\.[a-z]{2,}

# allow:
github.com
get.docker.com
```

## Команды

```
init        создать .gitpublic/ конфиг
scan        почистить во временный репозиторий, проверить, не пушить
publish     вычистить + запушить
hook        enable / disable / status
```

## Как работает `allow` / domains

Ничего не автоблокируется просто потому, что это домен.

`allow` используется только вместе со `scan`. Если `.gitpublic/scan` отсутствует или пустой, домены вообще не проверяются.

Чтобы блокировать похожие на домены строки, добавь широкий regex в `.gitpublic/scan`:

```
regex:[a-z0-9.-]+\.[a-z]{2,}
```

Теперь любой найденный домен валит scan, кроме случаев, когда сам найденный домен перечислен в `.gitpublic/allow`:

```
github.com
get.docker.com
example.com
```

`allow` не заменяет приватные домены. Для замены используй `.gitpublic/replace`:

```
private.company.local ==> example.com
regex:.*\.corp\.internal ==> example.com
```

Коротко:

| Что нужно | Файл |
|---|---|
| удалить файлы | `.gitpublic/ignore` |
| переписать приватный текст/домен/IP | `.gitpublic/replace` |
| упасть, если секрет/домен/IP выжил | `.gitpublic/scan` |
| разрешить публичные домены, найденные scan | `.gitpublic/allow` |

Больше примеров: [Advanced configuration](./docs/ADVANCED.md) / [RU](./docs/ADVANCED.ru.md).

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
