# Расширенная настройка

`git-private2public` читает либо папку `.gitpublic/`, либо один legacy YAML-файл.
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

Если какого-то файла нет, это значит “нет правил для этого шага”. Скрытых автоблокировок нет.

| Файл | Если есть | Если файла нет или он пустой |
|------|-----------|-------------------------------|
| `.gitpublic/config` | задаёт source, target и push-настройки | `source`/`target` обязательны, publish не запустится |
| `.gitpublic/ignore` | удаляет совпавшие файлы/пути из публичной истории | файлы не удаляются |
| `.gitpublic/replace` | заменяет совпавший текст в оставшихся файлах | текст не заменяется |
| `.gitpublic/scan` | валит publish, если после чистки остался совпавший pattern | ничего не блокируется на scan-шаге |
| `.gitpublic/allow` | разрешает конкретные найденные домены во время scan | ничего не разрешается |
| `.gitpublic/domains` | alias для `allow` | ничего не разрешается |

## `.gitpublic/config`

Пример:

```ini
source = you/private-repo
target = you/public-repo
push_force = true
push_branches = main
push_tags = false
```

`source` и `target` могут быть такими:

```text
you/repo                         # GitHub shorthand -> https://github.com/you/repo.git
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
| `regex:pattern ==> replacement` | regex replacement во всех text blobs |
| `glob:*.json:old ==> new` | replacement только в файлах, совпавших с glob |

Пробелы вокруг `==>` игнорируются.

Приватные домены надо переписывать именно через `replace`:

```text
internal.company.local ==> example.com
regex:.*\.corp\.internal ==> example.com
```

## `.gitpublic/scan`

`scan` — единственный шаг, который блокирует publish после чистки.

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
| `literal text` | fail, если exact text остался |
| `regex:pattern` | fail, если regex совпал |

Если `.gitpublic/scan` отсутствует или в нём нет активных правил, на этом шаге ничего не блокируется.

## Домены и `.gitpublic/allow`

Автоматического режима “заблокировать все домены” нет.

Домены блокируются только если ты сам добавил domain-rule в `.gitpublic/scan`, например:

```text
regex:[a-z0-9.-]+\.[a-z]{2,}
```

Это значит: “упасть на любой строке, похожей на домен”.

После этого `.gitpublic/allow` говорит, какие найденные домены нормальные:

```text
github.com
pypi.org
example.com
```

Важно:

- `allow` сам ничего не сканирует;
- `allow` ничего не заменяет;
- `allow` имеет смысл только если сработал `scan`;
- `allow` проверяется против самого найденного текста, а не против соседнего контекста;
- `domains` — просто alias-файл для `allow`.

### Примеры с доменами

Нет `.gitpublic/scan`:

```text
README содержит private.company.local
.gitpublic/allow пустой или отсутствует
```

Результат: publish не заблокирован, потому что scan-правила нет.

Широкий domain scan без allowlist:

```text
# scan
regex:[a-z0-9.-]+\.[a-z]{2,}
```

Результат: блокируется любой похожий на домен текст: `github.com`, `example.com`, `private.company.local` и так далее.

Широкий domain scan с allowlist:

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

Замена приватного домена + широкий scan:

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
| разрешить публичный домен, найденный широким domain scan | `allow` |
| автоблокировать все домены | добавь широкий domain regex в `scan` |
| автоблокировать секреты без правил | не поддерживается; напиши `scan` rules или запускай внешний scanner до publish |

## Hook mode

```bash
git-private2public hook enable
```

Ставит Git `pre-push` hook в текущий приватный repo. На каждый `git push` запускает:

```bash
git-private2public publish -c .gitpublic
```

Если уже есть чужой `pre-push` hook, тулза не перезаписывает его.

## YAML compatibility

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

Для реальных проектов лучше folder mode `.gitpublic/`.
