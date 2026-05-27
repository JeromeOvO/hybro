from common.dto import FileInfo


def file_info_to_legacy_response(file_info: FileInfo) -> dict:
    return file_info.model_dump(mode="python")


__all__ = ["file_info_to_legacy_response"]
