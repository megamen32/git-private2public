# git-private2public

**[English](./README.md)** · **[Русский](./README.ru.md)**

---

**Как `.gitignore`, только для публичности.**

У тебя приватный репо. Нужен публичный — без секретов. Эта тулза держит их в
синке. Автоматически.

## Быстрый старт

```bash
pip install git-filter-repo pyyaml
git-private2public init          # создаёт .git-private2public.yaml
```

Отредактируй конфиг — что прятать:

```yaml
source: you/private-repo
target: you/public-repo

ignore:
  - ".env"
  - "secrets/"
  - "*.key"
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

## Режимы

**Простой** — просто игнорировать файлы (пример выше).

**Средний** — ещё вычищать секреты внутри файлов:

```yaml
replace:
  - "10.0.0.5==>203.0.113.5"
  - "real-token==>***"
```

**Сложный** — регулярки, скан, отказаться пушить если что-то выжило:

```yaml
replace:
  - "regex:[A-Fa-f0-9]{64}==>***"
fail_on_match:
  - "regex:github_pat_[A-Za-z0-9_]{30,}"
  - "regex:192\\.168\\."
allow_domains:           # публичные URL которые ОК
  - "get.docker.com"
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
pip install git-filter-repo pyyaml
curl -fsSL https://raw.githubusercontent.com/megamen32/git-private2public/main/git-private2public.py \
  -o git-private2public && chmod +x git-private2public
```

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
