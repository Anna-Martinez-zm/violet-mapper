"""VioletMapper — JSON Schema to Python dataclass mapper."""

__version__ = "0.4.0"
__author__ = "Violet Mapper Maintainers"
__all__ = ["SchemaMapper", "DataclassTemplate", "MappedField", "MappingResult"]

from .mapper import SchemaMapper, MappedField, MappingResult
from .templates import DataclassTemplate
