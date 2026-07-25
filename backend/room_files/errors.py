class FileStorageError(Exception):
    """Base error for room file content operations."""


class FileConflictError(FileStorageError):
    """Raised when create-only publication finds an existing file."""


class FileTooLargeError(FileStorageError):
    """Raised when content exceeds a caller-provided bound."""


class FileUnavailableError(FileStorageError):
    """Raised when stored content is not a readable regular file."""


class FileOperationError(FileStorageError):
    """Raised when a local content operation fails unexpectedly."""
