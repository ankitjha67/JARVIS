"""Project scaffolding and analysis tools."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from pydantic import Field

from .. import host
from ..policy import Tier
from .registry import Args, ToolError, tool

_SAFE_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
_SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "__pycache__", ".tox", "dist", "build", ".next", ".cache"}

_GITIGNORE_PYTHON = """\
__pycache__/
*.py[cod]
.venv/
venv/
*.egg-info/
dist/
build/
.env
"""

_GITIGNORE_NODE = """\
node_modules/
dist/
build/
.env
*.log
"""


def _validate_project(name: str, parent: str) -> Path:
    if not _SAFE_NAME.match(name):
        raise ToolError("Project name must start with a letter and contain only letters, digits, hyphens and underscores.")
    parent_path = Path(parent).resolve()
    if not parent_path.is_dir():
        raise ToolError(f"Parent directory does not exist: {parent}")
    project = parent_path / name
    if project.exists():
        raise ToolError(f"Already exists: {project}")
    return project


# -- templates ---------------------------------------------------------------------------------

def _scaffold_python(root: Path, desc: str) -> list[str]:
    root.mkdir(parents=True)
    (root / "main.py").write_text(f'"""Entry point for {root.name}."""\n\n\ndef main():\n    print("Hello from {root.name}")\n\n\nif __name__ == "__main__":\n    main()\n', encoding="utf-8")
    (root / "requirements.txt").write_text("", encoding="utf-8")
    (root / ".gitignore").write_text(_GITIGNORE_PYTHON, encoding="utf-8")
    (root / "README.md").write_text(f"# {root.name}\n\n{desc or 'A Python project.'}\n", encoding="utf-8")
    return ["main.py", "requirements.txt", ".gitignore", "README.md"]


def _scaffold_fastapi(root: Path, desc: str) -> list[str]:
    root.mkdir(parents=True)
    (root / "main.py").write_text(f'"""FastAPI application: {root.name}."""\n\nfrom fastapi import FastAPI\n\napp = FastAPI(title="{root.name}")\n\n\n@app.get("/")\nasync def root():\n    return {{"message": "Hello from {root.name}"}}\n', encoding="utf-8")
    (root / "requirements.txt").write_text("fastapi>=0.110\nuvicorn[standard]>=0.29\n", encoding="utf-8")
    (root / ".gitignore").write_text(_GITIGNORE_PYTHON, encoding="utf-8")
    (root / "README.md").write_text(f"# {root.name}\n\n{desc or 'A FastAPI project.'}\n\n```bash\npip install -r requirements.txt\nuvicorn main:app --reload\n```\n", encoding="utf-8")
    return ["main.py", "requirements.txt", ".gitignore", "README.md"]


def _scaffold_flask(root: Path, desc: str) -> list[str]:
    root.mkdir(parents=True)
    (root / "app.py").write_text(f'"""Flask application: {root.name}."""\n\nfrom flask import Flask\n\napp = Flask(__name__)\n\n\n@app.route("/")\ndef index():\n    return "{root.name} is running"\n\n\nif __name__ == "__main__":\n    app.run(debug=True)\n', encoding="utf-8")
    (root / "requirements.txt").write_text("flask>=3.0\n", encoding="utf-8")
    (root / ".gitignore").write_text(_GITIGNORE_PYTHON, encoding="utf-8")
    (root / "README.md").write_text(f"# {root.name}\n\n{desc or 'A Flask project.'}\n\n```bash\npip install -r requirements.txt\npython app.py\n```\n", encoding="utf-8")
    return ["app.py", "requirements.txt", ".gitignore", "README.md"]


def _scaffold_node(root: Path, desc: str) -> list[str]:
    root.mkdir(parents=True)
    pkg = {"name": root.name, "version": "1.0.0", "description": desc or "", "main": "index.js",
           "scripts": {"start": "node index.js"}, "keywords": [], "license": "ISC"}
    (root / "package.json").write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")
    (root / "index.js").write_text(f'// {root.name}\nconsole.log("Hello from {root.name}");\n', encoding="utf-8")
    (root / ".gitignore").write_text(_GITIGNORE_NODE, encoding="utf-8")
    (root / "README.md").write_text(f"# {root.name}\n\n{desc or 'A Node.js project.'}\n", encoding="utf-8")
    return ["package.json", "index.js", ".gitignore", "README.md"]


def _scaffold_html(root: Path, desc: str) -> list[str]:
    root.mkdir(parents=True)
    (root / "index.html").write_text(f'<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="UTF-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n  <title>{root.name}</title>\n  <link rel="stylesheet" href="style.css">\n</head>\n<body>\n  <h1>{root.name}</h1>\n  <p>{desc or "Welcome."}</p>\n  <script src="script.js"></script>\n</body>\n</html>\n', encoding="utf-8")
    (root / "style.css").write_text("* { margin: 0; padding: 0; box-sizing: border-box; }\nbody { font-family: system-ui, sans-serif; padding: 2rem; }\n", encoding="utf-8")
    (root / "script.js").write_text(f'// {root.name}\nconsole.log("{root.name} loaded");\n', encoding="utf-8")
    return ["index.html", "style.css", "script.js"]


def _scaffold_cli(root: Path, name: str, cmd: list[str]) -> list[str]:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(root.parent),
                            creationflags=host.NO_WINDOW)
    if result.returncode != 0:
        raise ToolError(f"Scaffold command failed: {(result.stderr or result.stdout)[:500]}")
    if not root.exists():
        raise ToolError(f"Expected project directory was not created: {root}")
    return [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and not any(s in p.parts for s in _SKIP_DIRS)][:30]


_SCAFFOLDERS = {
    "python": _scaffold_python,
    "fastapi": _scaffold_fastapi,
    "flask": _scaffold_flask,
    "node": _scaffold_node,
    "html": _scaffold_html,
}

_CLI_TEMPLATES = {
    "react": lambda name: ["npm", "create", "vite@latest", name, "--", "--template", "react"],
    "nextjs": lambda name: ["npx", "create-next-app@latest", name, "--yes"],
}


class ProjectCreateArgs(Args):
    name: str = Field(..., min_length=1, max_length=64, description="Project name (letters, digits, hyphens)")
    type: str = Field(..., pattern="^(python|node|react|fastapi|flask|nextjs|html)$",
                      description="python, node, react, fastapi, flask, nextjs, or html")
    path: str = Field(..., min_length=1, max_length=400, description="Parent directory to create the project in")
    description: str = Field("", max_length=500, description="Short project description")


def _create_project(a: ProjectCreateArgs) -> dict:
    root = _validate_project(a.name, a.path)
    if a.type in _SCAFFOLDERS:
        files = _SCAFFOLDERS[a.type](root, a.description)
    elif a.type in _CLI_TEMPLATES:
        cmd = _CLI_TEMPLATES[a.type](a.name)
        files = _scaffold_cli(root, a.name, cmd)
    else:
        raise ToolError(f"Unknown project type: {a.type}")
    return {"created": str(root), "type": a.type, "files": files}


@tool("project_create", "Scaffold a new project: python, node, react, fastapi, flask, nextjs, or html.",
      ProjectCreateArgs, Tier.T2, sensitive=True,
      summary=lambda a: f"Create {a.type} project: {a.name}")
async def project_create(a: ProjectCreateArgs) -> dict:
    return await asyncio.to_thread(_create_project, a)


# -- project analysis --------------------------------------------------------------------------

class ProjectAnalyzeArgs(Args):
    path: str = Field(..., min_length=1, max_length=400, description="Project directory to analyze")


def _analyze(a: ProjectAnalyzeArgs) -> dict:
    root = Path(a.path).resolve()
    if not root.is_dir():
        raise ToolError(f"Not a directory: {a.path}")

    ext_counts: Counter[str] = Counter()
    total_files = 0
    total_size = 0

    for item in root.rglob("*"):
        if any(s in item.parts for s in _SKIP_DIRS):
            continue
        if item.is_file():
            total_files += 1
            total_size += item.stat().st_size
            ext_counts[item.suffix.lower() or "(no ext)"] += 1

    framework = "unknown"
    language = "unknown"
    entry_points: list[str] = []
    dependencies: list[str] = []

    if (root / "package.json").exists():
        language = "javascript/typescript"
        try:
            pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
            deps = list(pkg.get("dependencies", {}).keys())[:20]
            dependencies = deps
            if "next" in deps:
                framework = "next.js"
            elif "react" in deps or "react-dom" in deps:
                framework = "react"
            elif "vue" in deps:
                framework = "vue"
            elif "express" in deps:
                framework = "express"
            else:
                framework = "node"
            main = pkg.get("main")
            if main:
                entry_points.append(main)
        except (json.JSONDecodeError, OSError):
            pass
        if (root / "tsconfig.json").exists():
            language = "typescript"

    elif (root / "pyproject.toml").exists() or (root / "requirements.txt").exists() or (root / "setup.py").exists():
        language = "python"
        if (root / "requirements.txt").exists():
            try:
                deps = [l.split("==")[0].split(">=")[0].strip() for l in (root / "requirements.txt").read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
                dependencies = deps[:20]
            except OSError:
                pass
        if any(d in dependencies for d in ("fastapi", "FastAPI")):
            framework = "fastapi"
        elif any(d in dependencies for d in ("flask", "Flask")):
            framework = "flask"
        elif any(d in dependencies for d in ("django", "Django")):
            framework = "django"
        else:
            framework = "python"
        for name in ("main.py", "app.py", "manage.py", "run.py"):
            if (root / name).exists():
                entry_points.append(name)

    elif (root / "Cargo.toml").exists():
        language, framework = "rust", "cargo"
    elif (root / "go.mod").exists():
        language, framework = "go", "go module"
    elif (root / "index.html").exists():
        language, framework = "html/css/js", "static site"
        entry_points.append("index.html")

    top_extensions = dict(ext_counts.most_common(10))

    return {
        "path": str(root),
        "language": language,
        "framework": framework,
        "total_files": total_files,
        "total_size_mb": round(total_size / 1_048_576, 2),
        "file_types": top_extensions,
        "entry_points": entry_points,
        "dependencies": dependencies[:20],
    }


@tool("project_analyze", "Analyze a project directory: detect language, framework, file stats, dependencies and entry points.",
      ProjectAnalyzeArgs, Tier.T0,
      summary=lambda a: f"Analyze project: {a.path[:40]}")
async def project_analyze(a: ProjectAnalyzeArgs) -> dict:
    return await asyncio.to_thread(_analyze, a)
