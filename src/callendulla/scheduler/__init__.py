# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Scheduler service — fires due triggers and escalates ignored ones.

Lives in its own supervisord process alongside ``api`` and ``bot``.
Single-instance by design: multiple schedulers would race on
``triggers`` rows and double-fire. Replication is a future concern;
for now the docker-compose runs one container with one scheduler.

The package init does NOT re-export :func:`run` because importing
``scheduler.main`` eagerly pulls in :mod:`callendulla.bot`, which in
turn imports submodules that import back into :mod:`callendulla.scheduler`
(notably ``bot.handlers.reactions`` needs ``scheduler.quiet_hours``).
Eager re-export creates a circular import. Callers go directly:

    from callendulla.scheduler.main import run
"""
