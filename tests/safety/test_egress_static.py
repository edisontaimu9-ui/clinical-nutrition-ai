"""Static and runtime egress checks (NFR-SEC-09, NFR-ARC-01): no provider SDKs, network code only in the one transport module,
real-data build profile has no hosted adapter, and the mock path never opens a socket."""
import ast
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.safety
SDKS = {"openai", "anthropic", "cohere", "mistralai", "google", "groq", "together", "litellm", "langchain", "ollama", "boto3", "vertexai"}
NET = {"socket", "http", "urllib", "requests", "httpx", "aiohttp", "ssl", "ftplib", "smtplib", "websockets", "urllib3"}
ALLOWED_NET = {"packages/llm_hosted/src/cnai_llm_hosted/transport.py"}


def py_files():
    for d in ("packages", "scripts"):
        for p in (ROOT / d).rglob("*.py"):
            yield p


def top_imports(path):
    out = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module.split(".")[0])
    return out


def net_imports(path):
    """Top-level network packages imported, ignoring pure URL parsing (urllib.parse), which opens no connection."""
    out = set()
    for n in ast.walk(ast.parse(path.read_text())):
        names = [a.name for a in n.names] if isinstance(n, ast.Import) else \
            [n.module] if isinstance(n, ast.ImportFrom) and n.module and n.level == 0 else []
        for full in names:
            if full != "urllib.parse" and full.split(".")[0] in NET:
                out.add(full.split(".")[0])
        if isinstance(n, ast.ImportFrom) and n.module == "urllib":
            out |= {"urllib" for a in n.names if a.name != "parse"}
    return out


def test_no_provider_sdk_imports_anywhere():
    bad = [(str(p.relative_to(ROOT)), sorted(top_imports(p) & SDKS)) for p in py_files() if top_imports(p) & SDKS]
    assert not bad, bad


def test_network_libraries_only_in_transport():
    bad = [str(p.relative_to(ROOT)) for p in py_files()
           if str(p.relative_to(ROOT)) not in ALLOWED_NET and net_imports(p)]
    # scripts/migrate.py etc. use psycopg (database), not these libraries
    assert not bad, bad


def test_requirements_have_no_provider_sdk():
    text = (ROOT / "requirements-dev.txt").read_text().lower()
    assert not [s for s in SDKS if s in text.replace("-", "").replace("_", "")]


def test_real_data_profile_has_no_hosted_adapter():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/check_build_profile.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_build_profile_check_catches_a_hosted_import(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_build_profile as c
    f = tmp_path / "x.py"
    f.write_text("from cnai_llm_hosted.transport import HttpsTransport\n")
    assert c.imports_hosted(f)
    f.write_text("import cnai_llm_hosted\n")
    assert c.imports_hosted(f)
    f.write_text("import os\n")
    assert not c.imports_hosted(f)


def test_mock_path_opens_no_sockets(monkeypatch):
    from cnai_llm import corpus, summary_task as st
    from cnai_llm.gateway import Gateway
    from cnai_llm.registry import Registry
    from cnai_llm.types import GenerationRequest

    def boom(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    _, items = corpus.load_cases()
    g = Gateway(Registry.load(), environ={})
    cid, inp = items[0]
    assert g.generate(GenerationRequest(st.TASK_ID, cid, inp, "assessment_summary_v0")).ok
