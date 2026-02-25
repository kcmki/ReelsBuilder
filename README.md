# ReelsBuilder

> ReelsBuilder is a lightweight Python project for building short video reels from clips, with handlers for cloud and social platforms and optional Docker support.

## Overview

ReelsBuilder automates assembly of short videos from input clips, applies logos and basic processing, and produces ready-to-upload reels. It includes handlers for Google Drive, YouTube extraction, Instagram, Supabase/S3 storage, and a simple local filesystem workflow.

## Key Components

- `run_manager.py`: entrypoint for running the manager process that coordinates clip processing.
- `src/ClipBuilder.py`: primary clip assembly logic.
- `src/videoMaker.py`: video composition utilities and export routines.
- `src/DriveHandler.py`, `src/YoutubeExtractorHandler.py`, `src/InstagramHandler.py`, `src/SupabaseS3Handler.py`: platform-specific handlers.
- `src/lib.py`: shared helpers and utilities.
- `settings.ini`: runtime configuration (paths, credentials, flags).
- `clips/` and `reels/`: input clip storage and output reels.

## Installation

1. Create a Python 3.10+ virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate  # on Windows: .venv\\Scripts\\activate
```

2. Install dependencies (project uses a `requirements` file):

```bash
pip install -r requirements
```

## Configuration

Edit `settings.ini` to configure input/output paths, API keys, and optional flags (for example, disabling interactive prompts). Credentials for external services are stored under `src/credentials.json` or referenced from individual handlers.

## Usage

- Run the manager to process new clips:

```bash
python run_manager.py
```

- Common options: pass `--disable-interaction` to run non-interactively.

Processed reels are written to the `reels/` directory, one subfolder per source clip set.

## Docker

The repository includes a `Dockerfile` and `docker-compose.yml` for containerized runs. Build and start with:

```bash
docker-compose up --build
```

Adjust mounted volumes and `settings.ini` as needed for persistence and credential injection.

## Development Notes

- Source files live under `src/`. Keep handlers focused and small—they should only manage I/O and authentication, delegating processing to `ClipBuilder` and `videoMaker`.
- Temporary files are placed in `temp/` and can be cleaned regularly.

## Contributing

1. Fork the repo, create a feature branch, and open a PR.
2. Include small, focused changes and update `README.md` if behavior or config changes.

## License

Repository does not include an explicit license file. Add a `LICENSE` if you intend to publish or share.

---

If you'd like, I can also (a) expand sections with examples, (b) generate a `requirements.txt` from the `requirements` file, or (c) add a small usage example notebook. Which would you prefer next?
