# Monitoring Agent

Initial scaffold for a production-ready monitoring agent for an agentic AI reliability platform.

## Features

- Python 3.12
- FastAPI
- Async architecture
- Pydantic v2
- SOLID principles
- Type hints
- Structured logging
- pydantic-settings-based configuration
- Clean architecture structure

## Project Structure

```text
src/
  monitoring_agent/
    api/
      routes/
    application/
      use_cases/
    core/
    domain/
      entities/
      repositories/
      services/
    infrastructure/
      adapters/
      persistence/
    main.py
```

## Getting Started

1. Create a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Start the app with `uvicorn monitoring_agent.main:app`.
