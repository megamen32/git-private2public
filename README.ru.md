# git-private2public

**[English](./README.md)** · **[Русский](./README.ru.md)**

---

**Держи публичную копию своего приватного репо. Секреты вычищаются автоматически.**

Ты работаешь в приватном репо. Там реальные имена серверов, IP, токены, личные
конфиги. Ты хочешь ещё и публичный репо — но без утечек.

Эта тулза делает это за тебя. Каждый раз, когда запускаешь:

1. Копирует твой приватный репо
2. Удаляет файлы, которые ты сказал удалить
3. Заменяет секреты на `***`
4. Проверяет результат — не осталось ли чего
5. Пушит чистую версию в твой публичный репо

## За 30 секунд

```
pip install git-filter-repo pyyaml

git-private2public init          # создаёт файл конфига
# отредактируй .git-private2public.yaml — что удалять и заменять
git-private2public publish       # готово
```

Всё. Публичный репо чистый.

## Простой режим (просто игнорировать файлы)

Большинству нужно просто **не публиковать некоторые файлы**. Как `.gitignore`,
но для публичной версии.

```yaml
# .git-private2public.yaml
source: you/private-repo
target: you/public-repo

ignore:          # этих не будет в публичном репо. и всё.
  - ".env"
  - "secrets/"
  - "*.key"
  - "my-personal-notes.md"
  - "deploy/nginx/real-domain.conf"
```

Запусти `git-private2public publish`. Готово.

## Средний режим (ещё вычищать секреты внутри файлов)

Иногда в файле есть и публичное, и секрет. Например шаблон конфига с реальным IP.

```yaml
source: you/private-repo
target: you/public-repo

ignore:
  - ".env"

replace:         # найти → заменить, в содержимом файлов
  - "10.0.0.5==>203.0.113.5"       # реальный IP → пример
  - "real-token-xxx===>>>"         # токен → звёздочки
  - "my-server.example.com==>example.com"
```

## Сложный режим (регулярки, скан, CI, allowlists)

Для продвинутых. Полный конфиг со всеми опциями:

```yaml
source: you/private-repo
target: you/public-repo

# Файлы/папки для удаления из всей истории.
ignore:
  - "secrets/"
  - "*.env"
  - "*.key"

# Текст для замены внутри файлов. По умолчанию буквальный.
# Префикс "regex:" для регулярки. "glob:*.json:" для конкретного типа файлов.
replace:
  - "real-token==>***REMOVED***"
  - "regex:[A-Fa-f0-9]{64}==>***REMOVED***"        # ловит любой 64-символьный hex
  - "glob:*.json:secret==>x"                       # только в .json

# Домены, которые ОК публиковать (не триггерят скан ниже).
# Используй, чтобы публичные install URLs (типа get.docker.com) выжили.
allow_domains:
  - "get.docker.com"
  - "example.com"

# Отказаться пушить, если найдёт в финальном результате.
# Ловит то, что правила выше пропустили.
fail_on_match:
  - "regex:github_pat_[A-Za-z0-9_]{30,}"     # GitHub токены
  - "regex:sk-[A-Za-z0-9]{40,}"              # OpenAI ключи
  - "regex:192\\.168\\."                     # приватные IP
  - "regex:10\\.0\\.0\\."                    # приватные IP

push:
  force: true
  branches: [main]
```

## Команды

```
git-private2public init        # создать пример конфига
git-private2public scan        # проверить что будет (без пуша)
git-private2public publish     # вычистить + запушить в публичный репо
```

## Авторизация

Для GitHub задай токен, чтобы тулза могла пушить в твой публичный репо:

```bash
export GIT_PRIVATE2PUBLIC_TOKEN=ghp_xxx
git-private2public publish
```

Или вставь токен прямо в target URL в конфиге.

## Авто-запуск в CI

Добавь это в `.github/workflows/publish.yml` в **приватном** репо. Каждый
push в `main` → чистый публичный mirror.

```yaml
on:
  push:
    branches: [main]
jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - run: pip install git-filter-repo pyyaml
      - run: |
          curl -fsSL https://raw.githubusercontent.com/megamen32/git-private2public/main/git-private2public.py \
            -o git-private2public && chmod +x git-private2public
      - run: ./git-private2public publish -c .git-private2public.yaml
        env:
          GIT_PRIVATE2PUBLIC_TOKEN: ${{ secrets.PUBLIC_REPO_PAT }}
```

## Зачем

В Git и GitHub нет встроенного «сделать этот файл приватным в публичном репо».
Видимость — на уровне репозитория. Поэтому нужно два репо. Эта тулза держит
их в синке без утечек.

Другие тулзы делают часть работы:

| | удалить файлы | заменить текст | скан утечек | авто пуш | один конфиг |
|---|:---:|:---:|:---:|:---:|:---:|
| git-filter-repo | ✅ | ✅ | ❌ | ❌ | ❌ |
| BFG | ✅ | ✅ | ❌ | ❌ | ❌ |
| dupligit | ❌ | ❌ | ❌ | ✅ | ✅ |
| **git-private2public** | ✅ | ✅ | ✅ | ✅ | ✅ |

## Установка

```bash
pip install git-filter-repo pyyaml
curl -fsSL https://raw.githubusercontent.com/megamen32/git-private2public/main/git-private2public.py \
  -o git-private2public && chmod +x git-private2public
```

## Лицензия

MIT
