"""
CLI Console Formatter and Output Utilities.

Formats cards, tables, key-value lists, and alert banners for operator CLI.
Contains zero external dependencies.
"""

from typing import List, Dict, Any, Optional


from app.utils.logger import mask_phone


def print_header(title: str, subtitle: Optional[str] = None) -> None:
    """Prints a clear, sectioned header."""
    print()
    print("=" * 60)
    print(f" {title.upper()}")
    if subtitle:
        print(f" {subtitle}")
    print("=" * 60)


def print_card(title: str, data: Dict[str, Any]) -> None:
    """Prints a styled key-value card with aligned columns."""
    print()
    print(f"--- {title} " + "-" * max(0, 50 - len(title)))
    max_k_len = max((len(str(k)) for k in data.keys()), default=15)
    for key, value in data.items():
        k_str = str(key).ljust(max_k_len)
        print(f"  {k_str} : {value}")
    print("-" * 55)


def print_table(headers: List[str], rows: List[List[Any]]) -> None:
    """Prints a structured ASCII table with aligned columns."""
    if not headers:
        return

    # Calculate column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(val)))

    sep_line = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    header_str = "|" + "|".join(f" {headers[i].ljust(col_widths[i])} " for i in range(len(headers))) + "|"

    print()
    print(sep_line)
    print(header_str)
    print(sep_line)

    if not rows:
        empty_msg = " No records found "
        total_w = sum(col_widths) + (3 * len(col_widths)) - 1
        print("|" + empty_msg.center(total_w) + "|")
    else:
        for row in rows:
            row_str = "|" + "|".join(
                f" {str(row[i]).ljust(col_widths[i])} " if i < len(row) else f" {''.ljust(col_widths[i])} "
                for i in range(len(headers))
            ) + "|"
            print(row_str)

    print(sep_line)


def print_warning_box(message: str, title: str = "WARNING") -> None:
    """Renders a prominent warning banner for critical operator alerts."""
    lines = message.strip().split("\n")
    max_len = max([len(l) for l in lines] + [len(title) + 4, 50])
    border = "=" * (max_len + 4)

    print()
    print(f"+{border}+")
    print(f"|  [! {title} !]".ljust(max_len + 4) + "  |")
    print(f"+{border}+")
    for line in lines:
        print(f"|  {line.ljust(max_len)}  |")
    print(f"+{border}+")


def print_success(message: str) -> None:
    """Prints a success confirmation message."""
    print(f"[SUCCESS] {message}")


def print_warning(message: str) -> None:
    """Prints a formatted warning message."""
    print(f"[WARN] {message}")


def print_error(message: str) -> None:
    """Prints a formatted error message."""
    print(f"[ERROR] {message}")


def print_info(message: str) -> None:
    """Prints an informational message."""
    print(f"[INFO] {message}")

