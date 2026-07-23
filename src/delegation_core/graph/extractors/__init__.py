"""Per-language extractors, incrementally migrated out of graphify/extract.py.

Dispatch still flows through delegation_core.graph.extract (the facade re-exports every
moved name), so importing from delegation_core.graph.extract keeps working unchanged.
LANGUAGE_EXTRACTORS is the registry seed; wiring dispatch through it is a
later, separate step. See MIGRATION.md for how to port another language.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from delegation_core.graph.extractors.apex import extract_apex
from delegation_core.graph.extractors.bash import extract_bash
from delegation_core.graph.extractors.blade import extract_blade
from delegation_core.graph.extractors.dart import extract_dart
from delegation_core.graph.extractors.dm import extract_dm, extract_dmf, extract_dmi, extract_dmm
from delegation_core.graph.extractors.elixir import extract_elixir
from delegation_core.graph.extractors.fortran import extract_fortran
from delegation_core.graph.extractors.go import extract_go
from delegation_core.graph.extractors.json_config import extract_json
from delegation_core.graph.extractors.julia import extract_julia
from delegation_core.graph.extractors.markdown import extract_markdown
from delegation_core.graph.extractors.objc import extract_objc
from delegation_core.graph.extractors.pascal import extract_pascal
from delegation_core.graph.extractors.pascal_forms import extract_delphi_form, extract_lazarus_form
from delegation_core.graph.extractors.powershell import extract_powershell, extract_powershell_manifest
from delegation_core.graph.extractors.razor import extract_razor
from delegation_core.graph.extractors.rust import extract_rust
from delegation_core.graph.extractors.sln import extract_sln
from delegation_core.graph.extractors.sql import extract_sql
from delegation_core.graph.extractors.terraform import extract_terraform
from delegation_core.graph.extractors.verilog import extract_verilog
from delegation_core.graph.extractors.zig import extract_zig

LANGUAGE_EXTRACTORS: dict[str, Callable[[Path], dict]] = {
    "apex": extract_apex,
    "bash": extract_bash,
    "blade": extract_blade,
    "dart": extract_dart,
    "delphi_form": extract_delphi_form,
    "dm": extract_dm,
    "dmf": extract_dmf,
    "dmi": extract_dmi,
    "dmm": extract_dmm,
    "elixir": extract_elixir,
    "fortran": extract_fortran,
    "go": extract_go,
    "json": extract_json,
    "julia": extract_julia,
    "lazarus_form": extract_lazarus_form,
    "markdown": extract_markdown,
    "objc": extract_objc,
    "pascal": extract_pascal,
    "powershell": extract_powershell,
    "powershell_manifest": extract_powershell_manifest,
    "razor": extract_razor,
    "rust": extract_rust,
    "sln": extract_sln,
    "sql": extract_sql,
    "terraform": extract_terraform,
    "verilog": extract_verilog,
    "zig": extract_zig,
}
