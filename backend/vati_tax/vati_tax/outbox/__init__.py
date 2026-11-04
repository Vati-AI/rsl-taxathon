"""Durable outbox and idempotent delivery workers (TAX-033).

``policy`` holds the pure rules, ``store`` writes events in the caller's
transaction, ``worker`` delivers them and ``api`` exposes operator actions.
"""
