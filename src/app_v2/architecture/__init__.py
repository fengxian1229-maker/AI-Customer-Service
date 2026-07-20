"""Executable architecture constraints for V2."""

from app_v2.architecture.import_boundaries import ImportViolation, scan_forbidden_imports

__all__ = ["ImportViolation", "scan_forbidden_imports"]
