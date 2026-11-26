import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


LOADER = Path(__file__).with_name("amaps_task.py").resolve()


class TaskIntakeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.remote = self.directory / "remote.git"
        self.sender = self.directory / "sender"
        self.receiver = self.directory / "receiver"
        self.git("init", "--bare", str(self.remote), cwd=self.directory)
        self.git("init", "-b", "main", str(self.sender), cwd=self.directory)
        self.git("commit", "--allow-empty", "-m", "База", cwd=self.sender)
        self.git("clone", "-b", "main", str(self.sender), str(self.receiver), cwd=self.directory)
        self.git(
            "config", "--local", f"url.{self.remote}.insteadOf",
            "https://github.com/alexap2002-rgb/amaps-pro-site.git", cwd=self.receiver,
        )
        self.baseline = self.git("rev-parse", "HEAD", cwd=self.receiver)

    def git(self, *args, cwd):
        result = subprocess.run(
            ["git", "-c", "user.name=Тест", "-c", "user.email=test@example.invalid", *args],
            cwd=cwd, check=True, capture_output=True, text=True,
        )
        return result.stdout.strip()

    def publish_task(self, content=None):
        path = self.sender / ".github" / "amaps-task.md"
        path.parent.mkdir()
        if content is not None:
            path.write_bytes(content)
            self.git("add", ".github/amaps-task.md", cwd=self.sender)
        self.git("commit", "--allow-empty", "-m", "Контрольное ТЗ", cwd=self.sender)
        self.git("push", str(self.remote), "HEAD:refs/heads/amaps/tz-smoke", cwd=self.sender)
        return self.git("rev-parse", "HEAD", cwd=self.sender)

    def load(self, *args, env=None):
        return subprocess.run(
            [sys.executable, str(LOADER), *args], cwd=self.receiver,
            capture_output=True, text=True, env=env, timeout=15,
        )

    def issue_env(self, state, body="Синтетическое ТЗ"):
        gh = self.directory / "gh"
        issue = json.dumps({
            "number": 17, "state": state, "title": "Проверить приём", "body": body,
            "url": "https://github.com/alexap2002-rgb/amaps-pro-site/issues/17",
            "author": {"login": "test-owner"},
        })
        gh.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            "assert sys.argv[1:4] == ['issue', 'view', '17']\n"
            "assert sys.argv[sys.argv.index('--repo') + 1] == 'github.com/alexap2002-rgb/amaps-pro-site'\n"
            f"print({issue!r})\n",
            encoding="utf-8",
        )
        gh.chmod(0o755)
        return {**os.environ, "PATH": f"{self.directory}{os.pathsep}{os.environ['PATH']}"}

    def test_fetches_remote_task_without_switching_or_modifying_checkout(self):
        sha = self.publish_task("Синтетическое ТЗ\n".encode())
        result = self.load("--git", "smoke", "--expect-sha", sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(sha, result.stdout)
        self.assertIn("Синтетическое ТЗ", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.receiver), self.baseline)
        self.assertEqual(self.git("status", "--porcelain", cwd=self.receiver), "")

    def test_rejects_task_branch_pointing_to_unapproved_commit(self):
        self.publish_task("Синтетическое ТЗ".encode())
        result = self.load("--git", "smoke", "--expect-sha", self.baseline)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn(self.baseline, result.stderr)

    def test_ignores_local_replacement_of_approved_commit(self):
        sha = self.publish_task("Подлинное ТЗ".encode())
        self.assertEqual(self.load("--git", "smoke").returncode, 0)
        self.git("replace", sha, self.baseline, cwd=self.receiver)
        result = self.load("--git", "smoke", "--expect-sha", sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Подлинное ТЗ", result.stdout)

    def test_rejects_missing_task_ref(self):
        result = self.load("--git", "smoke")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("amaps/tz-smoke", result.stderr)

    def test_rejects_branch_with_no_task_file(self):
        self.publish_task()
        result = self.load("--git", "smoke")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn(".github/amaps-task.md", result.stderr)

    def test_rejects_oversized_task(self):
        self.publish_task(b"x" * 16385)
        result = self.load("--git", "smoke")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("16", result.stderr)

    def test_rejects_ref_syntax_as_task_identifier(self):
        result = self.load("--git", "../main")
        self.assertEqual(result.returncode, 2)
        self.assertIn("идентификатор", result.stderr.lower())

    def test_loads_open_issue_from_selected_repository(self):
        result = self.load("--issue", "17", env=self.issue_env("OPEN", "Синтетическое ТЗ\r\nВторая строка"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("/issues/17", result.stdout)
        self.assertIn("test-owner", result.stdout)
        self.assertIn("Синтетическое ТЗ\nВторая строка", result.stdout)

    def test_rejects_terminal_escape_in_task(self):
        self.publish_task(b"Title\n\x1b[2J")
        result = self.load("--git", "smoke")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("управляющие символы", result.stderr)

    def test_rejects_closed_issue(self):
        result = self.load("--issue", "17", env=self.issue_env("CLOSED"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("открыт", result.stderr)


if __name__ == "__main__":
    unittest.main()
