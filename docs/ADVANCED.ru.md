# Расширенная настройка

`git-private2public` читает либо папку `.gitpublic/`, либо один старый YAML-файл.
Рекомендуется папка `.gitpublic/`: в ней каждый файл отвечает за одну вещь.

## Главная модель

Публикация — это конвейер:

```text
приватный репозиторий
  -> временный clone
  -> удалить файлы из истории          (.gitpublic/ignore)
  -> заменить текст в оставшихся файлах (.gitpublic/replace)
  -> проверить финальный результат      (.gitpublic/scan + .gitpublic/allow)
  -> push в публичный репозиторий       (.gitpublic/config)
```

Если какого-то файла нет, это значит “нет пользовательских правил для этого шага”. Встроенная проверка известных учётных данных работает в publish, zero-config и guard-режимах.

| Файл | Если есть | Если файла нет или он пустой |
|------|-----------|-------------------------------|
| `.gitpublic/config` | задаёт source, target и push-настройки | `source`/`target` обязательны, publish не запустится |
| `.gitpublic/ignore` | удаляет совпавшие файлы/пути из публичной истории | файлы не удаляются |
| `.gitpublic/replace` | заменяет совпавший текст в оставшихся файлах | текст не заменяется |
| `.gitpublic/scan` | добавляет пользовательские блокирующие правила | встроенные правила продолжают работать в zero-config и guard-режимах |
| `.gitpublic/allow` | исключения для доменов, найденных проверкой `scan` | исключений для проверки нет |
| `.gitpublic/domains` | alias для `allow` | ничего не разрешается |
| `.gitpublic/public/` | добавляет или заменяет файлы в очищенном public-дереве | public-only overlay не применяется |

## `.gitpublic/config`

Пример:

```ini
source = you/private-repo
target = you/public-repo
push_force = true
push_branches = main
push_tags = false
```

Для первой публичной выкладки режим `snapshot` публикует только
очищенное текущее дерево tracked-файлов одним нейтральным root-коммитом.
История, авторы, сообщения коммитов, ветки и теги приватного repo в зеркало не попадают:

```ini
mode = snapshot
snapshot_include_source_sha = false
```

`snapshot_include_source_sha = true` ставьте только если нужно сознательно раскрыть SHA
приватного коммита. `snapshot` не разрешает `push_tags = true`. По умолчанию
остаётся обычный режим `history`.

`source` и `target` могут быть такими:

```text
you/repo                         # короткая форма GitHub -> https://github.com/you/repo.git
https://github.com/you/repo.git  # полный HTTPS URL
git@github.com:you/repo.git      # SSH URL
/path/to/local/repo              # локальный путь
```

`push_force = true` использует `--force-with-lease`. Это нормально для public mirror: его история часто переписывается после sanitization.

## `.gitpublic/ignore`

По одному правилу на строку, комментарии через `#`.

```gitignore
.env
secrets/
*.key
*.pem
private/*.json
```

Что делает:

- exact path удаляет конкретный файл из публикуемой истории;
- путь с `/` на конце удаляет директорию/префикс;
- glob вроде `*.key` или `private/*.json` удаляет совпавшие пути.

Это удаляет файлы во временном очищенном clone. Приватный репозиторий не меняется.

## `.gitpublic/public/`

Файлы из этой директории копируются в очищенное дерево после delete и
replace. Относительные пути сохраняются: `.gitpublic/public/README.md`
становится публичным `README.md`. Overlay-файлы попадают в public-коммит и
проходят ту же обязательную проверку секретов.

В `snapshot` overlay применяется до создания genesis, поэтом в публичном результате
по-прежнему ровно один root-коммит. Symlink, special-файлы, пути `.git` и выход
через destination path запрещены.

## `.gitpublic/replace`

По одной замене на строку:

```text
old text ==> new text
private.company.local ==> example.com
regex:192\.168\.[0-9]+\.[0-9]+ ==> 203.0.113.10
glob:*.json:real-token ==> ***
```

Форматы:

| Форма | Значение |
|-------|----------|
| `old ==> new` | literal replacement во всех text blobs |
| `regex:шаблоном ==> replacement` | regex replacement во всех text blobs |
| `glob:*.json:old ==> new` | replacement только в файлах, совпавших с glob |

Пробелы вокруг `==>` игнорируются.

Приватные домены надо переписывать именно через `replace`:

```text
internal.company.local ==> example.com
regex:.*\.corp\.internal ==> example.com
```

## `.gitpublic/scan`

Общую scan-политику можно отслеживать в Git, если каждое активное правило
начинается с `regex:`. Literal scan-правила должны оставаться untracked, потому что
сам literal может быть приватным. `.gitpublic/replace` всегда должен оставаться untracked.

`.gitpublic/scan` добавляет свои блокирующие правила. Встроенные правила известных секретов доступны без настройки в zero-config и guard-режимах.

По одному правилу на строку:

```text
regex:github_pat_[A-Za-z0-9_]{30,}
regex:sk-[A-Za-z0-9_-]{20,}
regex:192\.168\.
regex:[a-z0-9.-]+\.[a-z]{2,}
```

Форматы:

| Форма | Значение |
|-------|----------|
| `literal text` | ошибка, если exact text остался |
| `regex:шаблоном` | ошибка, если regex совпал |

Если `.gitpublic/scan` отсутствует, пользовательские правила не добавляются. Встроенная проверка всё равно работает в zero-config и guard-режимах.

## Домены и `.gitpublic/allow`

“Запретить всё с исключениями” включается только вручную. Автоматического режима “заблокировать все домены” нет.

Домены блокируются только если ты сам добавил правило для доменов в `.gitpublic/scan`, например:

```text
regex:[a-z0-9.-]+\.[a-z]{2,}
```

Это значит: “заблокировать публикацию, если после чистки осталась любая строка, похожая на домен”.

После этого `.gitpublic/allow` перечисляет исключения — какие найденные домены можно пропустить:

```text
github.com
pypi.org
example.com
```

Важно:

- `allow` сам ничего не сканирует; он отменяет ошибку `scan`, только если всё найденное значение точно равно разрешённому домену без учёта регистра;
- `allow` ничего не заменяет;
- `allow` имеет смысл только если сработала проверка `scan`;
- `allow` проверяется против самого найденного текста, а не против соседнего контекста;
- `domains` — просто файл-синоним для `allow`.

### Примеры с доменами

Нет `.gitpublic/scan`:

```text
README содержит private.company.local
.gitpublic/allow пустой или отсутствует
```

Результат: publish не заблокирован, потому что правила проверки нет.

Широкое правило проверки доменов без списка исключений:

```text
# scan
regex:[a-z0-9.-]+\.[a-z]{2,}
```

Результат: блокируется любой похожий на домен текст: `github.com`, `example.com`, `private.company.local` и так далее.

Широкое правило проверки доменов со списком исключений:

```text
# scan
regex:[a-z0-9.-]+\.[a-z]{2,}

# allow
github.com
example.com
```

Результат:

- `github.com` проходит;
- `example.com` проходит;
- `private.company.local` блокирует publish.

Замена приватного домена + широкая проверка доменов:

```text
# replace
private.company.local ==> example.com

# scan
regex:[a-z0-9.-]+\.[a-z]{2,}

# allow
github.com
example.com
```

Результат:

- `private.company.local` переписывается в `example.com`;
- `example.com` разрешён;
- publish проходит.

## `scan` vs `replace` vs `allow`

| Цель | Используй |
|------|-----------|
| удалить файлы | `ignore` |
| переписать приватное значение | `replace` |
| упасть, если значение пережило чистку | `scan` |
| сделать исключение для публичного домена, найденного широким правилом проверки доменов | `allow` |
| блокировать все похожие на домены строки | добавь широкое regex-правило для доменов в `scan` |
| блокировать известные форматы секретов | встроено, свои правила не обязательны |

## Режим Git hook

```bash
git-private2public hook enable
```

Ставит Git `pre-push` hook в текущий приватный repo. На каждый `git push` запускает:

```bash
git-private2public publish -c .gitpublic
```

Существующий пользовательский hook сохраняется и вызывается по цепочке. Guard и автопубликация могут работать одновременно.

## Совместимость с YAML

Legacy YAML config ещё работает:

```yaml
source: you/private-repo
target: you/public-repo
ignore:
  - .env
  - secrets/
replace:
  - private.company.local ==> example.com
fail_on_match:
  - regex:github_pat_[A-Za-z0-9_]{30,}
allow:
  - github.com
push:
  force: true
  branches: [main]
```

Для реальных проектов лучше режим папки `.gitpublic/`.
