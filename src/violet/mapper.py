"""
Configuration mapper and versioning engine.

Discovers configuration files (dotenv, YAML, TOML, JSON, INI, Dockerfile),
normalizes them into canonical templates, tracks versions with diffs,
and reconstructs historical configuration states.

Uses content-addressable storage with structural diff tracking
to efficiently store configuration changes over time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tomllib
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

logger = logging.getLogger("violet.mapper")


class ConfigFormat(Enum):
    DOTENV = auto()
    YAML = auto()
    TOML = auto()
    JSON = auto()
    INI = auto()
    DOCKERFILE = auto()
    UNKNOWN = auto()

    @classmethod
    def detect(cls, path: Path) -> "ConfigFormat":
        name = path.name.lower()
        ext = path.suffix.lower()
        if name in (".env", ".env.example", ".env.local", ".env.production",
                     ".env.development", ".env.staging"):
            return cls.DOTENV
        if ext in (".yaml", ".yml"):
            return cls.YAML
        if ext == ".toml":
            return cls.TOML
        if ext == ".json":
            return cls.JSON
        if ext in (".ini", ".cfg", ".conf"):
            return cls.INI
        if name.lower() == "dockerfile" or name.lower().startswith("dockerfile."):
            return cls.DOCKERFILE
        return cls.UNKNOWN


@dataclass
class DataclassTemplate:
    """A normalized, anonymized configuration template.

    Key-values are generalized: actual secrets/IPs/paths replaced
    with typed placeholders suitable for reuse as project scaffolding.
    """

    name: str
    format: ConfigFormat
    keys: List[str]
    placeholders: Dict[str, str]  # key → placeholder type
    raw_stripped: str  # Template with values replaced by {{PLACEHOLDER}}
    source_count: int = 0
    tags: List[str] = field(default_factory=list)


@dataclass
class MappingResult:
    """A point-in-time snapshot of a single configuration file."""

    path: str
    format: ConfigFormat
    content_hash: str  # SHA-256
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    keys: List[str] = field(default_factory=list)
    anonymized: str = ""  # Content with sensitive values redacted
    size_bytes: int = 0
    line_count: int = 0


class SchemaMapper:
    """Discovers, normalizes, snapshots, and versions configuration files.

    Mimics a versioned backup system for configuration drift tracking.
    Content is stored content-addressable (by hash) for dedup.
    """

    EXCLUDE_DIRS = {".git", ".hg", "node_modules", "__pycache__", ".venv",
                    "venv", "vendor", "target", "build", "dist"}

    MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB

    SENSITIVE_KEY_PATTERNS = [
        re.compile(p, re.IGNORECASE) for p in [
            r"(?:password|passwd|pwd|secret|token|key|credential|auth)",
            r"(?:api[_-]?key|api[_-]?secret|access[_-]?key)",
            r"(?:private[_-]?key|ssh[_-]?key|pgp[_-]?key)",
            r"(?:jwt[_-]?secret|encryption[_-]?key|signing[_-]?key)",
            r"(?:db[_-]?(?:password|pass|pwd))",
            r"(?:smtp[_-]?(?:password|pass))",
            r"(?:github[_-]?token|gitlab[_-]?token)",
        ]
    ]

    def __init__(self):
        self._store: Dict[str, MappingResult] = {}

    def discover(self, root: str | Path) -> List[Path]:
        """Find all configuration files in a directory tree."""
        root_path = Path(root).resolve()
        configs: List[Path] = []
        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [d for d in dirnames if d not in self.EXCLUDE_DIRS]
            for fname in filenames:
                fpath = Path(dirpath) / fname
                fmt = ConfigFormat.detect(fpath)
                if fmt != ConfigFormat.UNKNOWN:
                    if fpath.stat().st_size <= self.MAX_FILE_BYTES:
                        configs.append(fpath)
        logger.info("Discovered %d config files in %s", len(configs), root_path)
        return sorted(configs)

    def snapshot(self, filepath: Path) -> Optional[MappingResult]:
        """Capture a versioned snapshot of a configuration file."""
        fmt = ConfigFormat.detect(filepath)
        try:
            raw = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None
        content_hash = hashlib.sha256(raw.encode()).hexdigest()
        if content_hash in self._store:
            return self._store[content_hash]
        keys = self._extract_keys(raw, fmt)
        anonymized = self._anonymize(raw, fmt, keys)
        snap = MappingResult(
            path=str(filepath),
            format=fmt,
            content_hash=content_hash,
            keys=list(keys.keys()),
            anonymized=anonymized,
            size_bytes=len(raw.encode("utf-8")),
            line_count=raw.count("\n") + 1,
        )
        self._store[content_hash] = snap
        return snap

    def snapshot_directory(self, root: str | Path) -> List[MappingResult]:
        configs = self.discover(root)
        return [s for p in configs if (s := self.snapshot(p)) is not None]

    @staticmethod
    def _extract_keys(content: str, fmt: ConfigFormat) -> Dict[str, str]:
        """Extract configuration keys and their value types."""
        keys: Dict[str, str] = {}
        try:
            if fmt == ConfigFormat.YAML:
                import yaml
                data = yaml.safe_load(content)
                if isinstance(data, dict):
                    keys = SchemaMapper._flatten_keys(data)
            elif fmt == ConfigFormat.TOML:
                data = tomllib.loads(content)
                keys = SchemaMapper._flatten_keys(data)
            elif fmt == ConfigFormat.JSON:
                data = json.loads(content)
                if isinstance(data, dict):
                    keys = SchemaMapper._flatten_keys(data)
            elif fmt == ConfigFormat.DOTENV:
                for line in content.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        keys[k.strip()] = SchemaMapper._type_name(v.strip())
            elif fmt == ConfigFormat.INI:
                import configparser
                parser = configparser.ConfigParser()
                parser.read_string(content)
                for section in parser.sections():
                    for k, v in parser.items(section):
                        full = f"{section}.{k}"
                        keys[full] = SchemaMapper._type_name(v)
        except Exception:
            pass
        return keys

    def _anonymize(self, content: str, fmt: ConfigFormat,
                   keys: Dict[str, str]) -> str:
        """Replace sensitive values with [REDACTED] placeholders."""
        result = content
        for key in keys:
            for pat in self.SENSITIVE_KEY_PATTERNS:
                if pat.search(key):
                    if fmt == ConfigFormat.DOTENV:
                        result = re.sub(
                            rf'(^{re.escape(key)}\s*=\s*).*$',
                            rf'\1[REDACTED]', result,
                            flags=re.MULTILINE | re.IGNORECASE,
                        )
                    elif fmt in (ConfigFormat.YAML, ConfigFormat.JSON):
                        result = re.sub(
                            rf'("{re.escape(key)}"\s*:\s*)"[^"]*"',
                            rf'\1"[REDACTED]"', result,
                        )
        return result

    def diff_snapshots(self, a: MappingResult, b: MappingResult) -> Dict[str, Any]:
        """Compute structural diff between two snapshots."""
        keys_added = set(b.keys) - set(a.keys)
        keys_removed = set(a.keys) - set(b.keys)
        keys_common = set(a.keys) & set(b.keys)
        return {
            "hash_a": a.content_hash,
            "hash_b": b.content_hash,
            "keys_added": sorted(keys_added),
            "keys_removed": sorted(keys_removed),
            "keys_unchanged": len(keys_common),
            "total_keys_a": len(a.keys),
            "total_keys_b": len(b.keys),
        }

    @staticmethod
    def _flatten_keys(data: dict, prefix: str = "") -> Dict[str, str]:
        result: Dict[str, str] = {}
        for k, v in data.items():
            full = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                result.update(SchemaMapper._flatten_keys(v, full))
            else:
                result[full] = SchemaMapper._type_name(str(v))
        return result

    @staticmethod
    def _type_name(val: str) -> str:
        val = val.strip().strip('"').strip("'")
        if re.match(r'^\d+$', val):
            return "int"
        if re.match(r'^\d+\.\d+$', val):
            return "float"
        if val.lower() in ("true", "false", "yes", "no"):
            return "bool"
        if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?$', val):
            return "host:port"
        if re.match(r'^https?://', val):
            return "url"
        if re.match(r'^[a-f0-9]{32,}$', val, re.IGNORECASE):
            return "hex_token"
        if "@" in val and "." in val:
            return "email"
        return "string"
