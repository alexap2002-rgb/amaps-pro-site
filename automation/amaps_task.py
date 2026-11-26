"""Получить выбранное ТЗ АМАПС из GitHub и вывести его как текст."""

import argparse
import json
import re
import subprocess
import sys
import unicodedata


REPOSITORY = "alexap2002-rgb/amaps-pro-site"
GIT_URL = f"https://github.com/{REPOSITORY}.git"
TASK_PATH = ".github/amaps-task.md"
MAX_TASK_BYTES = 16 * 1024


class TaskError(Exception):
    pass


def read_command(args, failure):
    try:
        return subprocess.run(
            args, check=True, capture_output=True, timeout=30, stdin=subprocess.DEVNULL,
        ).stdout
    except FileNotFoundError as error:
        raise TaskError(f"Не найдена команда {args[0]}.") from error
    except (subprocess.SubprocessError, OSError) as error:
        raise TaskError(failure) from error


def task_text(value):
    if len(value) > MAX_TASK_BYTES:
        raise TaskError("Размер текста ТЗ превышает 16 КиБ.")
    try:
        text = value.decode("utf-8").replace("\r\n", "\n").strip()
    except UnicodeDecodeError as error:
        raise TaskError("ТЗ должно быть в кодировке UTF-8.") from error
    if not text:
        raise TaskError("В ТЗ отсутствует текст.")
    if any(unicodedata.category(character) in {"Cc", "Cf"} and character not in "\n\t" for character in text):
        raise TaskError("В ТЗ есть недопустимые управляющие символы.")
    return text


def read_git_task(identifier, expected_sha):
    git = ["git", "--no-replace-objects"]
    ref = f"refs/heads/amaps/tz-{identifier}"
    remote_ref = read_command(
        [*git, "ls-remote", "--exit-code", "--refs", GIT_URL, ref],
        f"Ветка {ref} недоступна в {REPOSITORY} или отсутствует.",
    ).strip()
    sha, separator, returned_ref = remote_ref.partition(b"\t")
    if not separator or returned_ref != ref.encode() or not re.fullmatch(rb"[0-9a-f]{40}", sha):
        raise TaskError(f"Не удалось однозначно определить коммит {ref}.")
    commit = sha.decode("ascii")
    if expected_sha is not None and commit != expected_sha:
        raise TaskError(f"Ветка {ref} указывает на {commit}. Ожидался согласованный SHA {expected_sha}.")
    read_command(
        [*git, "fetch", "--no-tags", "--no-write-fetch-head", GIT_URL, commit],
        f"Не удалось загрузить коммит {commit} из {REPOSITORY}.",
    )
    entry = read_command(
        [*git, "ls-tree", "-z", commit, "--", TASK_PATH],
        f"Не удалось прочитать дерево коммита {commit}.",
    )
    if not re.fullmatch(rb"100(?:644|755) blob [0-9a-f]{40}\t\.github/amaps-task\.md\x00", entry):
        raise TaskError(f"В коммите {commit} требуется обычный файл {TASK_PATH}.")
    object_name = f"{commit}:{TASK_PATH}"
    size = read_command([*git, "cat-file", "-s", object_name], "Не удалось определить размер ТЗ.")
    if int(size) > MAX_TASK_BYTES:
        raise TaskError("Размер текста ТЗ превышает 16 КиБ.")
    body = read_command([*git, "cat-file", "blob", object_name], "Не удалось прочитать текст ТЗ.")
    return f"Источник Git {REPOSITORY} {ref}\nКоммит {commit}\n\n{task_text(body)}"


def read_issue(number):
    raw = read_command(
        ["gh", "issue", "view", number, "--repo", f"github.com/{REPOSITORY}",
         "--json", "author,body,number,state,title,url"],
        f"Не удалось получить Issue #{number}. Проверьте доступ к GitHub API и вход в gh.",
    )
    expected_url = f"https://github.com/{REPOSITORY}/issues/{number}"
    try:
        data = json.loads(raw)
        author = data["author"]["login"]
        title = data["title"]
        body = data["body"]
        if data["number"] != int(number) or data["url"] != expected_url:
            raise ValueError
        if not all(isinstance(field, str) and field.strip() for field in (author, title, body)):
            raise ValueError
        state = data["state"]
    except (KeyError, TypeError, ValueError) as error:
        raise TaskError(f"Issue #{number} не содержит ожидаемых данных.") from error
    if state != "OPEN":
        raise TaskError(f"Issue #{number} должно быть открыто.")
    description = task_text(f"Автор {author}\n{title}\n\n{body}".encode())
    return f"Источник GitHub {expected_url}\n{description}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--git", metavar="ИД", help="прочитать ветку amaps/tz-ИД из GitHub")
    source.add_argument("--issue", metavar="НОМЕР", help="прочитать открытое Issue через gh")
    parser.add_argument("--expect-sha", metavar="SHA", help="проверить согласованный SHA при чтении ветки")
    args = parser.parse_args()
    if args.git is not None and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", args.git):
        parser.error("Идентификатор ТЗ должен состоять из 1–48 строчных букв a–z, цифр или дефисов.")
    if args.issue is not None and not re.fullmatch(r"[1-9][0-9]{0,9}", args.issue):
        parser.error("Номер Issue должен быть положительным целым числом до 10 цифр.")
    if args.expect_sha is not None and (args.git is None or not re.fullmatch(r"[0-9a-f]{40}", args.expect_sha)):
        parser.error("Параметр --expect-sha требует --git и полный SHA из 40 строчных шестнадцатеричных знаков.")
    try:
        print(read_git_task(args.git, args.expect_sha) if args.git is not None else read_issue(args.issue))
    except TaskError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
