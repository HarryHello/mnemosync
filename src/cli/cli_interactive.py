"""Mnemosync CLI 交互环境.

Thin re-export for backward compatibility.
The implementation has been moved to src.cli.interactive.
"""

from src.cli.interactive import MnemosyncCLI, main

__all__ = ["MnemosyncCLI", "main"]
