"""
Authentication and User Bootstrap CLI Commands.

Provides secure first-run OWNER account initialization directly via CLI
before public domain exposure.
"""

import getpass
from sqlalchemy.orm import Session

from app.cli.exit_codes import ExitCode
from app.cli.output import print_header, print_success, print_error, print_info
from app.web.services.bootstrap_service import BootstrapService


def handle_auth_bootstrap_owner(args, db: Session) -> ExitCode:
    """
    Executes 'outreach auth bootstrap-owner'.
    Creates the authoritative initial OWNER account.
    """
    print_header("Owner Account Bootstrap", "First-Run Administrative Provisioning")

    username = getattr(args, "username", None) or input("Enter OWNER username: ").strip()
    email = getattr(args, "email", None) or input("Enter OWNER email: ").strip()
    password = getattr(args, "password", None)
    password_prompt = getattr(args, "password_prompt", False)

    if password_prompt or not password:
        password = getpass.getpass("Enter OWNER password (min 12 chars, upper, lower, digit, symbol): ")
        password_confirm = getpass.getpass("Confirm OWNER password: ")
        if password != password_confirm:
            print_error("Passwords do not match.")
            return ExitCode.INVALID_ARGUMENT

    success, message, data = BootstrapService.bootstrap_owner(
        db=db,
        username=username,
        email=email,
        password=password,
        ip_address="127.0.0.1 (CLI)"
    )

    if not success:
        print_error(f"Bootstrap failed: {message}")
        return ExitCode.GENERAL_ERROR

    print_success(f"OWNER account '{username}' successfully bootstrapped!")
    print_info(f"User ID: {data.get('user_id')}")
    print_info(f"Email: {email}")
    print_info("First-run setup route (/setup) is now permanently locked.")
    return ExitCode.SUCCESS
