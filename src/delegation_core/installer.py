"""
installer.py — install, update and uninstall as one implementation.

Until now this project had no `update` at all. Updating meant re-running
install.sh or install.bat, which those scripts do handle (they back up the
previous package, keep config.json, and re-register the service), but with two
gaps that only show up on a machine that is already running:

  1. Neither script STOPS the daemon before `pip install` writes into the venv
     the daemon is running from. On Linux and macOS that survives, and both
     scripts print "Restart delegation-core to load the new code", so the user
     is left holding a manual step. On Windows pip cannot replace a file another
     process holds open, and the line that follows is `>nul 2>&1` with no
     errorlevel check, so a half-applied upgrade has no way of being noticed.

  2. The same six tasks are written twice, once in bash and once in batch, and
     they have already drifted: uninstall.sh checks the result of what it
     removes, uninstall.bat does not.

This module is the single implementation. `update()` lands first because it is
the operation that did not exist; install and uninstall follow into the same
place, and the shell scripts shrink to finding a Python and calling in.

Nothing here touches the vault or the downloaded model weights. Ever.
"""

from __future__ import annotations

import filecmp
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from . import service
from .config import CONFIG_DIR

logger = logging.getLogger("installer")

#: Documents copied to CONFIG_DIR so the MCP clients can point at a stable path
#: regardless of where the source checkout lives.
SHIPPED_DOCS = ("AGENT_GUIDE.md", "CLAUDE_SYSTEM_PROMPT.md")


# ── where this install came from ─────────────────────────────────────────────

def _dist_info() -> Path | None:
    """The installed package's .dist-info directory, or None."""
    for parent in Path(__file__).resolve().parents:
        if parent.name == "site-packages":
            hits = sorted(parent.glob("delegation_core-*.dist-info"))
            return hits[0] if hits else None
    # Editable installs put the package outside site-packages, so walk the
    # interpreter's own path instead of the module's.
    for entry in sys.path:
        candidato = Path(entry)
        if candidato.name == "site-packages":
            hits = sorted(candidato.glob("delegation_core-*.dist-info"))
            if hits:
                return hits[0]
    return None


def source_root() -> Path | None:
    """The directory this package was installed FROM, or None if unknowable.

    Read from `direct_url.json`, which pip writes per PEP 610 whenever a
    package is installed from a path or a VCS. On this machine it holds
    `{"dir_info": {"editable": true}, "url": "file:///home/joey/Projects/delegation-core"}`.

    Deliberately not guessed from `__file__`: that works for an editable
    install, where the module really does live in the checkout, and gives the
    wrong answer for a normal one, where it points into site-packages and the
    original source directory is not derivable from it at all. A wrong source
    root here would make `update()` pull in the wrong place, or reinstall the
    copy it is trying to replace.
    """
    info = _dist_info()
    if info is None:
        return None
    try:
        dados = json.loads((info / "direct_url.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    url = str(dados.get("url", ""))
    if not url.startswith("file://"):
        return None                      # installed from an index or a VCS URL
    caminho = Path(url[len("file://"):])
    return caminho if caminho.is_dir() else None


def is_editable() -> bool:
    """True when the venv points at the source tree instead of copying it."""
    info = _dist_info()
    if info is None:
        return False
    try:
        dados = json.loads((info / "direct_url.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(dados.get("dir_info", {}).get("editable"))


# ── git, when the source is a checkout ───────────────────────────────────────

def _git(root: Path, *args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, "git not found"
    except subprocess.TimeoutExpired:
        return 124, f"git timed out after {timeout}s"


def is_git_checkout(root: Path) -> bool:
    return (root / ".git").exists()


def git_state(root: Path) -> dict:
    """Branch, commit and how far behind its upstream, without changing anything.

    `fetch` is a read: it updates the remote-tracking ref and touches no file in
    the working tree. Counting commits without fetching first would report
    "up to date" from a stale ref, which is the answer a user is least able to
    detect as wrong.
    """
    if not is_git_checkout(root):
        return {"git": False}

    _, branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    _, commit = _git(root, "rev-parse", "--short", "HEAD")
    sujo = _git(root, "status", "--porcelain")[1]

    codigo_fetch, saida_fetch = _git(root, "fetch", "--quiet")
    upstream_code, upstream = _git(root, "rev-parse", "--abbrev-ref", "@{upstream}")

    atras = adiante = None
    if upstream_code == 0:
        codigo, contagem = _git(root, "rev-list", "--left-right", "--count",
                                f"{upstream}...HEAD")
        if codigo == 0 and "\t" in contagem:
            esq, dir_ = contagem.split("\t")[:2]
            atras, adiante = int(esq), int(dir_)

    return {
        "git": True,
        "branch": branch,
        "commit": commit,
        "dirty": bool(sujo.strip()),
        "dirty_files": len([ln for ln in sujo.splitlines() if ln.strip()]),
        "upstream": upstream if upstream_code == 0 else None,
        "behind": atras,
        "ahead": adiante,
        "fetch_ok": codigo_fetch == 0,
        "fetch_detail": "" if codigo_fetch == 0 else saida_fetch,
    }


# ── shipped docs and hooks ───────────────────────────────────────────────────

def refresh_shipped_files(root: Path) -> dict:
    """Copy AGENT_GUIDE/CLAUDE_SYSTEM_PROMPT and the hooks into CONFIG_DIR.

    Never clobbers a file the user changed: theirs is kept and the shipped copy
    lands beside it as `<name>.dist.<ext>` so the two can be diffed on purpose.

    **Compares before declaring a customisation**, which is where install.sh
    was wrong. Its hook loop used `cmp -s` and only wrote `.dist.py` on a real
    difference; its document helper wrote `.dist.md` whenever the destination
    merely existed. So every upgrade told the user "AGENT_GUIDE.md already
    present: kept yours" about a file they had never touched, and left a
    redundant copy behind as if there were something to reconcile.

    Measured on this machine before the fix: `AGENT_GUIDE.md` and
    `AGENT_GUIDE.dist.md` were byte-identical at 35.857 bytes, and
    `CLAUDE_SYSTEM_PROMPT.md` and its `.dist.md` at 6.148. The hooks directory,
    doing the same job correctly, had produced no `.dist.py` at all.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / "hooks").mkdir(parents=True, exist_ok=True)
    resultado: dict = {"installed": [], "kept_yours": [], "unchanged": [], "missing": []}

    def _copiar(origem: Path, destino: Path, rotulo: str) -> None:
        if not origem.is_file():
            resultado["missing"].append(rotulo)
            return
        if not destino.exists():
            shutil.copyfile(origem, destino)
            resultado["installed"].append(rotulo)
            return
        if filecmp.cmp(origem, destino, shallow=False):
            resultado["unchanged"].append(rotulo)
            return
        lado = destino.with_suffix(f".dist{destino.suffix}")
        shutil.copyfile(origem, lado)
        resultado["kept_yours"].append(rotulo)

    for nome in SHIPPED_DOCS:
        _copiar(root / nome, CONFIG_DIR / nome, nome)

    for hook in sorted((root / "hooks").glob("*.py")) if (root / "hooks").is_dir() else []:
        _copiar(hook, CONFIG_DIR / "hooks" / hook.name, f"hooks/{hook.name}")

    return resultado


def stale_dist_copies() -> list[str]:
    """`.dist` files that are identical to the file they sit beside.

    These are the leftovers of the bug above: nothing to reconcile, but they
    read as if there were. Reported rather than deleted, because a `.dist` file
    is the user's to remove.
    """
    sobras = []
    for base in CONFIG_DIR.glob("*.dist.*"):
        real = base.with_name(base.name.replace(".dist", "", 1))
        if real.is_file() and filecmp.cmp(base, real, shallow=False):
            sobras.append(str(base.relative_to(CONFIG_DIR)))
    for base in (CONFIG_DIR / "hooks").glob("*.dist.*"):
        real = base.with_name(base.name.replace(".dist", "", 1))
        if real.is_file() and filecmp.cmp(base, real, shallow=False):
            sobras.append(str(base.relative_to(CONFIG_DIR)))
    return sorted(sobras)


# ── the update itself ────────────────────────────────────────────────────────

def _pip_install(root: Path) -> tuple[bool, str]:
    """Reinstall the package from `root`, with the optional extras if they build."""
    base = [sys.executable, "-m", "pip", "install", "--quiet"]
    for alvo in (f"{root}[graph,web]", str(root)):
        try:
            p = subprocess.run([*base, alvo], capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            return False, "pip timed out after 30 minutes"
        if p.returncode == 0:
            return True, alvo
        ultimo = (p.stdout + p.stderr).strip()
    return False, ultimo


def update(check_only: bool = False, restart: bool = True,
           root: Path | None = None) -> dict:
    """Bring the installed package up to date with its source, safely.

    The order is the point:

      1. find the source, and refuse rather than guess if it cannot be found;
      2. report what would change; stop here when `check_only`;
      3. STOP the daemon, so pip is not writing under a live process;
      4. pull, reinstall, refresh the shipped docs and hooks;
      5. re-register the service, then start it and wait until it answers.

    A step that fails stops the sequence, and the daemon is put back up
    regardless. An update that fails halfway must not leave the machine with no
    service: that would be worse than not having updated at all.

    `restart=False` leaves the daemon stopped at the end, for a caller that is
    about to do more work before bringing it back.
    """
    raiz = root or source_root()
    passos: list[dict] = []

    def _passo(nome: str, ok: bool, **extra) -> dict:
        registro = {"step": nome, "ok": ok, **extra}
        passos.append(registro)
        return registro

    if raiz is None:
        return {
            "status": "cannot_locate_source",
            "steps": passos,
            "detail": (
                "This install has no recorded source directory (pip's "
                "direct_url.json is absent or does not point at a path). "
                "Download a fresh copy and run the installer in it."
            ),
        }

    from . import __version__ as versao_antes
    estado = git_state(raiz)
    _passo("locate_source", True, root=str(raiz), editable=is_editable(), **estado)

    if check_only:
        atras = estado.get("behind")
        return {
            "status": ("behind" if atras else "up_to_date") if estado.get("git")
                      else "unknown_not_a_checkout",
            "version": versao_antes,
            "root": str(raiz),
            "steps": passos,
        }

    if estado.get("git") and estado.get("dirty"):
        return {
            "status": "refused_dirty_checkout",
            "steps": passos,
            "detail": (
                f"{estado['dirty_files']} uncommitted change(s) in {raiz}. "
                "Committing or stashing them is your call, not this command's: "
                "a pull here could conflict or discard work."
            ),
        }

    estava_no_ar = service.is_up()
    parada = service.stop()
    if parada["status"] == "failed":
        _passo("stop_service", False, **parada)
        return {"status": "failed", "steps": passos,
                "detail": "Refusing to write into the venv while the daemon is still up."}
    _passo("stop_service", True, **parada)

    resultado = {"status": "ok", "version_before": versao_antes, "root": str(raiz)}
    try:
        if estado.get("git"):
            codigo, saida = _git(raiz, "pull", "--ff-only")
            _passo("git_pull", codigo == 0, detail=saida)
            if codigo != 0:
                resultado["status"] = "failed"
                resultado["detail"] = (
                    "git pull --ff-only failed. Fast-forward only is deliberate: "
                    "merging or rebasing your branch is your decision.\n" + saida
                )
                return resultado

        ok, detalhe = _pip_install(raiz)
        _passo("pip_install", ok, detail=detalhe)
        if not ok:
            resultado["status"] = "failed"
            resultado["detail"] = f"pip install failed:\n{detalhe}"
            return resultado

        _passo("refresh_docs_and_hooks", True, **refresh_shipped_files(raiz))
        _passo("register_service", True, **service.install())
    finally:
        # Whatever happened above, the machine does not get left without a
        # daemon it had before this command ran.
        if restart and estava_no_ar:
            partida = service.start()
            pronto = service.is_up(wait_seconds=60)
            _passo("start_service", partida["status"] == "started" and pronto,
                   ready=pronto, **partida)
            if not pronto:
                resultado["status"] = "started_but_not_answering"
                resultado.setdefault(
                    "detail",
                    "The service manager accepted the start but the daemon did "
                    "not answer on its port within 60s. Check its log.",
                )

    resultado["steps"] = passos
    resultado["stale_dist_copies"] = stale_dist_copies()
    return resultado
