"""Route modules — one per agent, file ownership is exclusive.

Each module exposes `ROUTES = [(method, path, handler), ...]`. server.py
imports every module and concatenates their tables into one dispatcher; it is
the only file that does so. See agents/CONTRACTS.md §6 for the prefix each
module owns and agents/AGENT_0_FOUNDATION.md for how this split came about.
"""
