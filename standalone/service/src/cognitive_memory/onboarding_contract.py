"""Machine-readable bootstrap contract; loaded only when schema tooling needs it."""
from typing import Any,Literal
from pydantic import BaseModel,ConfigDict


class BootstrapManifest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    manifest_version: Literal["mnemosyne.bootstrap.v1"]
    id: str
    name: str
    version: str
    source: dict[str,Any]
    compatibility: dict[str,Any]
    dependencies: list[str]
    permissions: list[str]
    installation: dict[str,Any]
    integration: dict[str,Any]
    lifecycle: list[str]
    recovery: dict[str,Any]
    capabilities: list[str]
    checksums: dict[str,str]
    signatures: list[str]
