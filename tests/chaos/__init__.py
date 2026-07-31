"""Layer-3 chaos + cross-module soak tests.

These tests inject randomised failures (delays, exceptions, cancellations,
disconnects) across module boundaries — MessageBus × ConversationStore ×
ClientPool × CronService × orchestrator — hunting for bugs that only
surface when independent subsystems interact under duress.

Every test is deterministic (seeded RNG, deterministic iteration counts)
so failures replay cleanly.  Non-flakiness is a hard requirement: a
chaos test that passes 99/100 times is a chaos test that hides bugs.
"""
