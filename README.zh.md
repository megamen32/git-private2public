# git-private2public

[![PyPI version](https://img.shields.io/pypi/v/git-private2public.svg)](https://pypi.org/project/git-private2public/) [![CI](https://github.com/megamen32/git-private2public/actions/workflows/ci.yml/badge.svg)](https://github.com/megamen32/git-private2public/actions/workflows/ci.yml)

**[English](./README.md)** · **[Русский](./README.ru.md)** · **简体中文**

![git-private2public 仓库审查工作台](docs/assets/readme-hero.png)

**像 `.gitignore` 一样，但控制哪些内容可以公开。**

## 快速开始

项目需要 Python 3.9+。使用一条命令安装 CLI 及其 Git 清理引擎：

```bash
python3 -m pip install git-private2public
```

在要同步的私有仓库中初始化配置：

```bash
git-private2public init
```

编辑 `.gitpublic/config` 设置 `source` 与 `target`，再编辑 `.gitpublic/ignore` 添加不应公开的文件：

```text
.env
secrets/
*.key
```

## 安全发布

先执行 dry-run 查看计划，再发布：

```bash
git-private2public publish --dry-run
git-private2public publish
```

工具基于 `git-filter-repo` 和 YAML 规则创建干净的公开镜像。完整规则见 [高级配置](./docs/ADVANCED.md)。

## 许可证

MIT
