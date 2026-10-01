check:
    uv run ruff check edgar tests scripts
    uv run pytest -q

run:
    uv run python -m edgar.server

smoke-retrieval:
    uv run python scripts/smoke_retrieval.py

# Requires the user to complete app-specific consent in the local browser first.
# Select each adapter in the UI and record only non-sensitive evidence.
