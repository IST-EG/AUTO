"""
Unit tests for CLI argument parser and routing.
"""

import pytest
from app.cli.main import build_parser, main
from app.cli.exit_codes import ExitCode


def test_parser_session_subcommands():
    parser = build_parser()

    # session login
    args = parser.parse_args(["session", "login", "--timeout", "60", "--headless"])
    assert args.command == "session"
    assert args.subcommand == "login"
    assert args.timeout == 60
    assert args.headless is True

    # session status
    args = parser.parse_args(["session", "status"])
    assert args.command == "session"
    assert args.subcommand == "status"

    # session logout
    args = parser.parse_args(["session", "logout", "--clear-cache"])
    assert args.command == "session"
    assert args.subcommand == "logout"
    assert args.clear_cache is True


def test_parser_campaign_subcommands():
    parser = build_parser()

    # campaign run (business transition only, no --daemon)
    args = parser.parse_args(["campaign", "run", "42"])
    assert args.command == "campaign"
    assert args.subcommand == "run"
    assert args.campaign_id == 42
    assert not hasattr(args, "daemon")

    # campaign status
    args = parser.parse_args(["campaign", "status", "42"])
    assert args.command == "campaign"
    assert args.subcommand == "status"
    assert args.campaign_id == 42

    # campaign pause
    args = parser.parse_args(["campaign", "pause", "42"])
    assert args.command == "campaign"
    assert args.subcommand == "pause"
    assert args.campaign_id == 42

    # campaign resume
    args = parser.parse_args(["campaign", "resume", "42"])
    assert args.command == "campaign"
    assert args.subcommand == "resume"
    assert args.campaign_id == 42

    # campaign stop
    args = parser.parse_args(["campaign", "stop", "42", "--confirm"])
    assert args.command == "campaign"
    assert args.subcommand == "stop"
    assert args.campaign_id == 42
    assert args.confirm is True


def test_parser_runner_subcommands():
    parser = build_parser()

    # runner start requires --campaign-id
    args = parser.parse_args(["runner", "start", "--campaign-id", "5", "--poll-interval", "2", "--headless"])
    assert args.command == "runner"
    assert args.subcommand == "start"
    assert args.campaign_id == 5
    assert args.poll_interval == 2
    assert args.headless is True

    # runner status
    args = parser.parse_args(["runner", "status"])
    assert args.command == "runner"
    assert args.subcommand == "status"

    # runner stop
    args = parser.parse_args(["runner", "stop"])
    assert args.command == "runner"
    assert args.subcommand == "stop"


def test_parser_queue_subcommands():
    parser = build_parser()

    # queue status
    args = parser.parse_args(["queue", "status", "--campaign-id", "10"])
    assert args.command == "queue"
    assert args.subcommand == "status"
    assert args.campaign_id == 10

    # queue inspect
    args = parser.parse_args(["queue", "inspect", "99"])
    assert args.command == "queue"
    assert args.subcommand == "inspect"
    assert args.message_id == 99

    # queue reconcile
    args = parser.parse_args(["queue", "reconcile", "--campaign-id", "10"])
    assert args.command == "queue"
    assert args.subcommand == "reconcile"
    assert args.campaign_id == 10

    # queue override requires --reason and has no --force
    args = parser.parse_args(["queue", "override", "99", "--reason", "Contact confirmed did not receive"])
    assert args.command == "queue"
    assert args.subcommand == "override"
    assert args.message_id == 99
    assert args.reason == "Contact confirmed did not receive"
    assert not hasattr(args, "force")


def test_parser_emergency_commands():
    parser = build_parser()

    # emergency-stop
    args = parser.parse_args(["emergency-stop", "--reason", "Test incident"])
    assert args.command == "emergency-stop"
    assert args.reason == "Test incident"

    # emergency-status
    args = parser.parse_args(["emergency-status"])
    assert args.command == "emergency-status"

    # emergency-resume
    args = parser.parse_args(["emergency-resume", "--reason", "All clear"])
    assert args.command == "emergency-resume"
    assert args.reason == "All clear"


def test_main_help_returns_success(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_main_no_command_returns_success(capsys):
    ret = main([])
    assert ret == int(ExitCode.SUCCESS)
