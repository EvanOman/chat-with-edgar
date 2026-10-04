check:
    uv run ruff check edgar tests scripts
    uv run pytest -q

run:
    uv run python -m edgar.server

smoke-retrieval:
    uv run python scripts/smoke_retrieval.py

# Requires app-specific consent in this existing browser. At most six user turns.
verify-live session model:
    uv run python scripts/verify_live.py --session {{quote(session)}} --model {{quote(model)}}
