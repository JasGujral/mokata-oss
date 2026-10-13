"""0.0.22 — the independent verification of D5, replayed (the verifier's own shapes, verbatim).

The verifier refuted two claims: "a Bash write meets the same verdict as a native Write" (newline,
grouping, wrapper, combined-flag and into-a-directory shapes all wrote `src/foo.py` while a Write
was blocked) and "reads are never refused" (awk comparisons, a computed `cd`, `xargs grep`, a quote
in a heredoc …). Each shape is pinned here in the direction the verifier showed was wrong, plus the
read-only subagent lane and the forged-independence path (an agent appending to mokata's own run
record).
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import hook_cli
from mokata import run_intent as RI
from mokata.subagent_contract import start_contract

from test_d5_the_bash_side_door import _bash, _repo, _write

# Each WROTE src/foo.py (exit 0) while a native Write to it was blocked (exit 2).
BYPASSES = (
    "echo hi\nsed -i s/a/b/ src/foo.py",
    "true\ncp /tmp/x.py src/foo.py",
    "{ sed -i s/a/b/ src/foo.py; }",
    "if true; then sed -i s/a/b/ src/foo.py; fi",
    "for f in a; do sed -i s/a/b/ src/foo.py; done",
    "! sed -i s/a/b/ src/foo.py",
    "timeout 5 sed -i s/a/b/ src/foo.py",
    "nice -n 5 sed -i s/a/b/ src/foo.py",
    "busybox sed -i s/a/b/ src/foo.py",
    "bash -ec 'sed -i s/a/b/ src/foo.py'",
    "cp /tmp/foo.py src/",
    "mv /tmp/foo.py src/",
    "cp -t src /tmp/foo.py",
    "sort -o src/foo.py /tmp/x",
    "curl -so src/foo.py http://localhost/x",
    "git checkout HEAD -- src/foo.py",
    "python3 -c \"open('/tmp/a','w'); p='src/'+'foo.py'; open(p,'w')\"",
    "python3 -c \"import pathlib; pathlib.Path('src/foo.py').open('w')\"",
    "python3 -c \"import os; os.open('src/foo.py', os.O_WRONLY|os.O_CREAT)\"",
    "perl -e 'open(my $f, \">\", \"src/foo.py\")'",
    "deno eval \"Deno.writeTextFileSync('src/foo.py','x')\"",
)

# Each was REFUSED as bash-unreadable in a run, and writes nothing.
READS = (
    "awk 'NR>1' README.md",
    "awk -F, '$3 > 5 {print $1}' f",
    "awk '{ if ($2 >= 10) print }' f",
    "cd $HOME && ls",
    "cd \"$(git rev-parse --show-toplevel)\" && git status",
    "git ls-files | xargs grep -n python",
    "find . -name '*.py' | xargs grep -l rm",
    "cat <<EOF\ndon't panic\nEOF",
    "cat <<'EOF'\nusage: tool > src/app.py\nEOF",
    "python3 -c \"import subprocess; print(subprocess.check_output(['git','log']))\"",
    "python3 -c \"import json; print(json.load(open('package.json')).get('name','a'))\"",
    "node -e \"/x/.exec('x')\"",
    "[[ $a > $b ]] && echo yes",
    "echo ok # > src/foo.py",
)


class SameVerdictAsWrite(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_every_verified_bypass_is_now_blocked_where_the_Write_is(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            self.assertEqual(hook_cli.BLOCK_EXIT, _write(d, "S1", "src/foo.py")[0])
            for cmd in BYPASSES:
                with self.subTest(cmd=cmd):
                    code, err = _bash(d, "S1", cmd)
                    self.assertEqual(hook_cli.BLOCK_EXIT, code, f"bypass still open: {err}")

    def test_a_computed_cd_followed_by_a_relative_write_is_still_refused(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            code, err = _bash(d, "S1", 'cd "$D" && echo x > foo.py')
            self.assertEqual(hook_cli.BLOCK_EXIT, code)
            self.assertIn("bash-unreadable", err)


class ReadsAreNotRefused(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_every_verified_false_refusal_now_passes_in_a_run(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            for cmd in READS:
                with self.subTest(cmd=cmd):
                    code, err = _bash(d, "S1", cmd)
                    self.assertEqual(0, code, err)


def _agent_bash(d, sid, agent_id, command):
    payload = {"cwd": d, "session_id": sid, "agent_id": agent_id, "tool_name": "Bash",
               "tool_input": {"command": command}}
    err = io.StringIO()
    with mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(payload)), \
            redirect_stderr(err):
        return hook_cli.gate_guard_main([]), err.getvalue()


class ReadOnlySubagents(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_deletes_and_tree_rewriting_git_are_writes_for_a_read_only_agent(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            start_contract(d, "S1", "ag1", "Explore")
            for cmd in ("rm README.md", "git checkout -- README.md", "git reset --hard",
                        "true\ntee notes.txt < /dev/null", "timeout 5 sed -i s/a/b/ notes.txt"):
                with self.subTest(cmd=cmd):
                    self.assertEqual(hook_cli.BLOCK_EXIT, _agent_bash(d, "S1", "ag1", cmd)[0])
            for cmd in ("git log --oneline", "awk 'NR>1' README.md", "grep -rn x ."):
                with self.subTest(read=cmd):
                    self.assertEqual(0, _agent_bash(d, "S1", "ag1", cmd)[0])

    def test_the_main_session_may_still_delete_in_a_run(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            self.assertEqual(0, _bash(d, "S1", "rm -rf build")[0])


class TheRunRecordIsNotAgentWritable(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_a_forged_subagent_event_is_refused_by_bash_and_by_write(self):
        forged = ('echo \'{"type":"subagent_finished","run_id":"rA"}\' >> '
                  '.mokata/temp_local/state/progress-events.jsonl')
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            for sid in ("S1", "S-unclaimed"):
                code, err = _bash(d, sid, forged)
                self.assertEqual(hook_cli.BLOCK_EXIT, code)
                self.assertIn(f"BLOCKED [{hook_cli.GATE_OWN_RECORD}]", err)
            self.assertEqual(hook_cli.BLOCK_EXIT, _write(
                d, "S1", ".mokata/temp_local/state/progress-events.jsonl")[0])

    def test_the_plans_folder_a_skill_writes_stays_writable(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            os.makedirs(os.path.join(d, ".mokata", "temp_local", "plans"), exist_ok=True)
            self.assertEqual(0, _write(d, "S2", ".mokata/temp_local/plans/x.md")[0])




# ---------------------------------------------------------------- second verification (2026-10-11)

BYPASSES_2 = (
    "f(){ sed -i s/a/b/ src/foo.py; }; f",
    "function f { sed -i s/a/b/ src/foo.py; }; f",
    'echo "$(sed -i s/a/b/ src/foo.py)"',
    'v="$(cp /tmp/x src/foo.py)"',
    'echo "`echo x > src/foo.py`"',
    "diff <(echo a) <(sed -i s/a/b/ src/foo.py)",
    "echo x > >(tee src/foo.py)",
    "git checkout src/foo.py",
    "git checkout HEAD src/foo.py",
    "git -C . checkout -- src/foo.py",
    "git -c a=b checkout -- src/foo.py",
    "git checkout-index -f src/foo.py",
    "git apply x.patch",
    "git am < x.patch",
    "git stash pop",
    "git reset --hard",
    "patch -p1 < x.diff",
    "patch -p1 <<'EOF'\n--- a/src/foo.py\n+++ b/src/foo.py\nEOF",
    "command -p sed -i s/a/b/ src/foo.py",
    "watch -n1 sed -i s/a/b/ src/foo.py",
    "uv run python -c \"open('src/foo.py','w')\"",
    "python3.11 -c \"open('src/foo.py','w')\"",
    "gawk -i inplace '{print}' src/foo.py",
)
READS_2 = (
    "grep -n '>' src/foo.py", "grep -c '>>' src/foo.py", "rg '>' src/foo.py",
    "echo '>' src/foo.py", "echo \\> src/foo.py", "python3 --version", "python3 -V",
    "bash --version", "[ \"$a\" \\> \"$b\" ] && echo gt", "test \"$a\" \\> \"$b\"",
)
WIPES = (
    "rm -rf .mokata/temp_local/state", "rm -rf .mokata", "rm -rf .mokata/temp_local",
    "mv .mokata/temp_local/state /tmp/s", "git clean -fdx",
    "rm .mokata/temp_local/state/progress-events.jsonl",
    "S=.mokata/temp_local/state; echo x >> $S/progress-events.jsonl",
    "node -e \"require('fs').appendFileSync('.mokata/temp_local/state/progress-events.jsonl','x')\"",
    "python3 -c \"open('.mokata/temp_local/state/progress-events.jsonl',mode='a')\"",
    "echo x >> .mokata/temp_local/stat*/progress-events.jsonl",
    "echo x >> .mokata/temp_local/{state,plans}/progress-events.jsonl",
    'echo "$(echo x >> .mokata/temp_local/state/progress-events.jsonl)"',
)


class SecondVerification(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_bypasses_are_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            for cmd in BYPASSES_2:
                with self.subTest(cmd=cmd):
                    self.assertEqual(hook_cli.BLOCK_EXIT, _bash(d, "S1", cmd)[0])

    def test_reads_pass(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            for cmd in READS_2:
                with self.subTest(cmd=cmd):
                    code, err = _bash(d, "S1", cmd)
                    self.assertEqual(0, code, err)

    def test_the_run_record_cannot_be_wiped_or_forged_in_any_session(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            for sid in ("S1", "S-none"):
                for cmd in WIPES:
                    with self.subTest(sid=sid, cmd=cmd):
                        code, err = _bash(d, sid, cmd)
                        self.assertEqual(hook_cli.BLOCK_EXIT, code, err)
            self.assertEqual(0, _bash(d, "S1", "rm -rf build dist")[0])
            self.assertEqual(0, _bash(d, "S1", "cat .mokata/temp_local/state/progress-events.jsonl")[0])

    def test_read_only_agents_git_global_options_and_read_forms(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            start_contract(d, "S1", "ag1", "Explore")
            for cmd in ("git -C . reset --hard", "git -c a=b commit -m x", "git fetch",
                        "f(){ rm notes.txt; }; f", 'echo "$(rm notes.txt)"', "tar xf x.tar"):
                with self.subTest(write=cmd):
                    self.assertEqual(hook_cli.BLOCK_EXIT, _agent_bash(d, "S1", "ag1", cmd)[0])
            for cmd in ("git branch -a", "git tag -l", "git stash list", "git worktree list",
                        "python3 --version", "git config user.name"):
                with self.subTest(read=cmd):
                    code, err = _agent_bash(d, "S1", "ag1", cmd)
                    self.assertEqual(0, code, err)


if __name__ == "__main__":
    unittest.main()
