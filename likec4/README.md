# Dockerized LikeC4 CLI + Live Editor

Container image that bundles the LikeC4 CLI with Graphviz (built from source) and Playwright/Chromium. The entrypoint is `likec4` and the working directory inside the container is `/data`. A lightweight Node server (in this repo) serves a VS Code–style editor so you can edit a `.c4` file and reload the diagram without leaving the browser.

## Tech used
- Docker/Docker Compose to build and run the LikeC4 CLI image.
- Node.js 22.23.4 LTS server (plain http + fs) to serve the editor UI and proxy file read/write.
- Graphviz built from source (latest release) for rendering diagrams.
- Playwright + Chromium runtime pulled during image build (required by LikeC4).
- LikeC4 CLI installed globally in the image.
- Monaco Editor (CDN) for the VS Code–style in-browser editor.

## Build the image
- Build locally:  
  ```bash
  docker build -t likec4-cli .
  ```
- Optional: pin versions while building (defaults are latest):  
  ```bash
  docker build \
    -t likec4-cli \
    --build-arg NODE_VERSION=22.23.4 \
    --build-arg GRAPHVIZ_VERSION=10.0.1 \
    --build-arg PLAYWRIGHT_VER=1.56.1 \
    --build-arg LIKEC4_VER=0.66.0 \
    .
  ```

## Run the CLI directly
Mount your LikeC4 project into `/data` so exports and servers use your local files.

- Show help:  
  ```bash
  docker run --rm -it likec4-cli
  ```
- Export diagrams (writes into your mounted project directory):  
  ```bash
  docker run --rm -it -v "$PWD:/data" likec4-cli export
  ```
- Serve the viewer (ports exposed by the image: `5173`, `24678`):  
  ```bash
  docker run --rm -it \
    -v "$PWD:/data" \
    -p 5173:5173 -p 24678:24678 \
    likec4-cli dev --host 0.0.0.0
  ```
  Then open `http://localhost:5173/`.

If you need another LikeC4 subcommand, append it after `likec4-cli` in `docker run`—the entrypoint passes all arguments through to the CLI.

## Live editor + diagram (Docker Compose)
The included `docker-compose.yml` runs two things inside one container: the LikeC4 dev server on `5173` and the editor server on `4173`.

- Start everything (builds the image if needed):  
  ```bash
  docker compose up --build
  ```
- Open `http://localhost:4173/` to edit `likec4/default.c4` in a Monaco/VS Code–style editor and see the diagram beside it.  
  - `Save & Render` (or `Ctrl/Cmd + S`) writes to SeaweedFS and regenerates the official LikeC4 webcomponent bundle so the local viewer reloads.  
  - Rendering happens client-side via the LikeC4 webcomponent bundle served by the editor server.

### Tweaks
- Edit a different file: set `LIKEC4_C4_FILE` before `docker compose up`, e.g. `LIKEC4_C4_FILE=data/yourfile.c4 docker compose up`.
- Override build args when needed:  
  ```bash
  GRAPHVIZ_VERSION=10.0.1 PLAYWRIGHT_VER=1.56.1 LIKEC4_VER=0.66.0 docker compose build
  ```
