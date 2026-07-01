"""Tests for Everlight config mapper and template templates."""

import tempfile
from pathlib import Path

from violet.mapper import SchemaMapper, ConfigFormat, MappingResult, DataclassTemplate
from violet.templates import FieldLibrary


class TestConfigFormat:
    def test_detect_dotenv(self):
        assert ConfigFormat.detect(Path(".env")) == ConfigFormat.DOTENV
        assert ConfigFormat.detect(Path(".env.example")) == ConfigFormat.DOTENV

    def test_detect_yaml(self):
        assert ConfigFormat.detect(Path("config.yaml")) == ConfigFormat.YAML
        assert ConfigFormat.detect(Path("settings.yml")) == ConfigFormat.YAML

    def test_detect_toml(self):
        assert ConfigFormat.detect(Path("pyproject.toml")) == ConfigFormat.TOML

    def test_detect_json(self):
        assert ConfigFormat.detect(Path("package.json")) == ConfigFormat.JSON

    def test_detect_ini(self):
        assert ConfigFormat.detect(Path("setup.cfg")) == ConfigFormat.INI

    def test_detect_dockerfile(self):
        assert ConfigFormat.detect(Path("Dockerfile")) == ConfigFormat.DOCKERFILE

    def test_detect_unknown(self):
        assert ConfigFormat.detect(Path("main.py")) == ConfigFormat.UNKNOWN


class TestSchemaMapper:
    def test_discover_finds_env(self):
        mapper = SchemaMapper()
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("DATABASE_URL=postgres://localhost\nSECRET_KEY=abc123\n")
            configs = mapper.discover(tmp)
            assert len(configs) == 1
            assert configs[0].name == ".env"

    def test_snapshot_dotenv(self):
        mapper = SchemaMapper()
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("HOST=0.0.0.0\nPORT=8080\nSECRET_KEY=topsecret\n")
            snap = mapper.snapshot(env_path)
            assert snap is not None
            assert snap.format == ConfigFormat.DOTENV
            assert "HOST" in snap.keys
            assert "PORT" in snap.keys
            assert "SECRET_KEY" in snap.keys

    def test_snapshot_deduplicates(self):
        mapper = SchemaMapper()
        with tempfile.TemporaryDirectory() as tmp:
            env1 = Path(tmp) / ".env"
            env1.write_text("KEY=value\n")
            snap1 = mapper.snapshot(env1)
            snap2 = mapper.snapshot(env1)
            assert snap1 is snap2

    def test_extract_keys_yaml(self):
        keys = SchemaMapper._extract_keys(
            "server:\n  host: localhost\n  port: 8080\n",
            ConfigFormat.YAML,
        )
        assert "server.host" in keys
        assert "server.port" in keys

    def test_type_name_detection(self):
        assert SchemaMapper._type_name("8080") == "int"
        assert SchemaMapper._type_name("3.14") == "float"
        assert SchemaMapper._type_name("true") == "bool"
        assert SchemaMapper._type_name("192.168.1.1:8080") == "host:port"
        assert SchemaMapper._type_name("https://example.com") == "url"


class TestFieldLibrary:
    def test_ingest_and_most_common(self):
        lib = FieldLibrary()
        snap1 = MappingResult(
            path="/a/.env", format=ConfigFormat.DOTENV,
            content_hash="abc", keys=["HOST", "PORT"],
            anonymized="HOST=localhost\nPORT=8080",
        )
        snap2 = MappingResult(
            path="/b/.env", format=ConfigFormat.DOTENV,
            content_hash="def", keys=["HOST", "PORT", "DEBUG"],
            anonymized="HOST=0.0.0.0\nPORT=3000\nDEBUG=true",
        )
        snap3 = MappingResult(
            path="/c/.env", format=ConfigFormat.DOTENV,
            content_hash="ghi", keys=["HOST", "PORT"],
            anonymized="HOST=10.0.0.1\nPORT=9090",
        )
        lib.ingest([snap1, snap2, snap3])
        top = lib.most_common(5)
        assert len(top) == 2
        assert top[0].source_count == 2
        assert top[0].keys == ["HOST", "PORT"]
