"""Regression coverage for manager/worker launch argument and CLI wiring."""

import shlex
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dcam import cli, orchestrator, tmux

SESSION = "team-alpha"
PROJECT = "/tmp/manager's project $(unchanged); space"
BRIEF = "Audit user's paths; preserve $(literal) and `literal`\nThen report evidence."
BINARY = "/opt/Claude Apps/user's claude"


def launch_argv(command):
    """Validate the shell boundary without executing a launch command."""
    argv = shlex.split(command)
    assert len(argv) == 4, "executable, system flag, system prompt and initial user prompt"
    assert argv[1] == "--append-system-prompt"
    assert argv[2].strip()
    assert argv[3].strip(), "a role prompt alone leaves interactive agents waiting"
    return argv


@pytest.mark.parametrize("role", ["manager", "dev", "reviewer"])
def test_launch_builders_preserve_executable_and_seed_initial_turn(role):
    if role == "manager":
        command = tmux.build_manager_launch_cmd(BINARY, session=SESSION,
                                                project_path=PROJECT, brief=BRIEF)
    elif role == "dev":
        command = tmux.build_dev_launch_cmd("audit", BRIEF, BINARY, session=SESSION)
    else:
        command = tmux.build_reviewer_launch_cmd(BINARY, extra_context=BRIEF,
                                                 session=SESSION)
    argv = launch_argv(command)
    assert argv[0] == BINARY
    assert SESSION in argv[2]
    assert BRIEF in (argv[3] if role == "manager" else argv[2])
    if role == "manager":
        assert PROJECT in argv[2]


def test_manager_without_brief_still_has_initial_setup_turn():
    argv = launch_argv(tmux.build_manager_launch_cmd(session=SESSION,
                                                     project_path=PROJECT))
    assert SESSION in argv[2]
    assert PROJECT in argv[2]
    assert "objective" in argv[3].lower()


def test_builders_remain_compatible_with_original_positional_calls():
    assert launch_argv(tmux.build_manager_launch_cmd("claude"))[0] == "claude"
    assert BRIEF in launch_argv(tmux.build_dev_launch_cmd("audit", BRIEF, "claude"))[2]
    assert BRIEF in launch_argv(tmux.build_reviewer_launch_cmd("claude", BRIEF))[2]


@pytest.fixture
def fake_runtime(monkeypatch):
    """All startup/storage/task hooks are mocked; no agent or issue is created."""
    bootstrap = Mock()
    start = Mock(side_effect=lambda session, project, manager_cmd=None: session)
    dev = Mock(side_effect=lambda session, slug, project, dev_cmd=None:
               f"{session}:dev-{slug}")
    review = Mock(side_effect=lambda session, project, review_cmd=None:
                  f"{session}:review")
    monkeypatch.setattr(cli.claude_code, "bootstrap_claude_first_run", bootstrap)
    monkeypatch.setattr(tmux, "start_session", start)
    monkeypatch.setattr(tmux, "spawn_dev_window", dev)
    monkeypatch.setattr(tmux, "spawn_review_window", review)
    monkeypatch.setattr(tmux, "_tmux_available", lambda: True)
    monkeypatch.setattr(tmux, "session_exists", lambda session: True)
    monkeypatch.setattr(tmux, "list_windows", lambda session: [])
    monkeypatch.setattr(cli, "get_store", Mock(return_value=object()))
    monkeypatch.setattr(orchestrator, "list_tasks", Mock(return_value=[]))
    monkeypatch.setattr(orchestrator, "create_task", Mock(return_value=None))
    return SimpleNamespace(bootstrap=bootstrap, start=start, dev=dev, review=review)


def run_cli(monkeypatch, args):
    monkeypatch.setattr(cli.sys, "argv", ["dcam", *args])
    cli.main()


def manual_command(output):
    command = output[output.index("To start"):].split(": ", 1)[1]
    outer = shlex.split(command.rsplit(" Enter", 1)[0] + " Enter")
    assert outer[:3] == ["tmux", "send-keys", "-t"]
    assert len(outer) == 6, "the complete Claude invocation must be one send-keys argument"
    assert outer[-1] == "Enter"
    return outer[3], outer[4]


@pytest.mark.parametrize("launch", [False, True])
def test_start_cli_passes_brief_project_session_and_launch_mode(monkeypatch, capsys,
                                                              fake_runtime, launch):
    args = ["tmux", "start", SESSION, "--project", PROJECT,
            "--brief", BRIEF, "--claude-bin", "claude"]
    if launch:
        args.append("--launch")
    run_cli(monkeypatch, args)
    expected = tmux.build_manager_launch_cmd("claude", session=SESSION,
                                             project_path=PROJECT, brief=BRIEF)
    fake_runtime.start.assert_called_once_with(SESSION, PROJECT,
                                               manager_cmd=expected if launch else None)
    if launch:
        fake_runtime.bootstrap.assert_called_once_with(PROJECT)
        assert "To start" not in capsys.readouterr().out
    else:
        fake_runtime.bootstrap.assert_not_called()
        target, command = manual_command(capsys.readouterr().out)
        assert target == f"{SESSION}:manager"
        assert command == expected
    argv = launch_argv(expected)
    assert PROJECT in argv[2]
    assert SESSION in argv[2]
    assert BRIEF in argv[3]


@pytest.mark.parametrize("role", ["dev", "review"])
@pytest.mark.parametrize("launch", [False, True])
def test_worker_cli_launch_and_manual_commands_are_equivalent(monkeypatch, capsys,
                                                             fake_runtime, role, launch):
    if role == "dev":
        args = ["tmux", "dev", SESSION, "audit", BRIEF, "--no-task"]
        expected = tmux.build_dev_launch_cmd("audit", BRIEF, "claude", session=SESSION)
        spawn = fake_runtime.dev
        command_key = "dev_cmd"
        target = f"{SESSION}:dev-audit"
    else:
        args = ["tmux", "review", SESSION, "--no-bootstrap"]
        expected = tmux.build_reviewer_launch_cmd("claude", session=SESSION)
        spawn = fake_runtime.review
        command_key = "review_cmd"
        target = f"{SESSION}:review"
    args += ["--project", PROJECT]
    if launch:
        args.append("--launch")
    run_cli(monkeypatch, args)
    assert spawn.call_count == 1
    assert spawn.call_args.args[0] == SESSION
    assert spawn.call_args.args[-1] == PROJECT
    assert spawn.call_args.kwargs[command_key] == (expected if launch else None)
    if launch:
        fake_runtime.bootstrap.assert_called_once_with(PROJECT)
    else:
        fake_runtime.bootstrap.assert_not_called()
        manual_target, command = manual_command(capsys.readouterr().out)
        assert manual_target == target
        assert command == expected
    assert SESSION in launch_argv(expected)[2]


def test_dev_existing_window_and_task_do_not_create_or_launch_again(monkeypatch,
                                                                   fake_runtime):
    existing = SimpleNamespace(id="task-1")
    monkeypatch.setattr(tmux, "list_windows", lambda session: ["dev-audit"])
    orchestrator.list_tasks.return_value = [existing]
    run_cli(monkeypatch, ["tmux", "dev", SESSION, "audit", BRIEF, "--launch",
                          "--project", PROJECT])
    orchestrator.list_tasks.assert_called_once_with(status="open",
                                                    labels=["role:dev", "slug:audit"])
    orchestrator.create_task.assert_not_called()
    fake_runtime.dev.assert_called_once_with(SESSION, "audit", PROJECT, dev_cmd=None)


def test_dev_duplicate_task_state_refuses_launch_without_force(monkeypatch,
                                                               fake_runtime):
    orchestrator.list_tasks.return_value = [SimpleNamespace(id="task-1"),
                                            SimpleNamespace(id="task-2")]
    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, ["tmux", "dev", SESSION, "audit", BRIEF, "--launch"])
    assert exc.value.code == 1
    fake_runtime.dev.assert_not_called()
    orchestrator.create_task.assert_not_called()
