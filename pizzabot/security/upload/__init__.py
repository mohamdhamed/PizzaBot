"""طبقة الأمان للملفات الواردة. ابدأ من validator.validate_file()"""

from pizzabot.security.upload.validator import validate_file, ValidationResult

__all__ = ["validate_file", "ValidationResult"]
