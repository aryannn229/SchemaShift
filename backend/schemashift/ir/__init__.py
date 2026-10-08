"""Intermediate representation of schema guarantees."""

from schemashift.ir.builder import build_ir
from schemashift.ir.nodes import IRProgram
from schemashift.ir.printer import print_ir

__all__ = ["IRProgram", "build_ir", "print_ir"]
