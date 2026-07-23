from dal.runtime_store.parts.parsing import _strip_file_urls


class TestStripFileUrls:
    def test_strip_from_insert_doc(self):
        doc = {
            "message_content": {
                "message_text": "hi",
                "attachments": [
                    {"file_id": "f1", "s3_key": "k1", "file_url": "https://presigned"},
                    {"file_id": "f2", "s3_key": "k2"},
                ],
            }
        }
        _strip_file_urls(doc)
        for att in doc["message_content"]["attachments"]:
            assert "file_url" not in att

    def test_strip_from_update_doc(self):
        doc = {
            "$set": {
                "message_content": {
                    "attachments": [
                        {"file_id": "f1", "file_url": "https://url"},
                    ]
                }
            }
        }
        _strip_file_urls(doc)
        assert "file_url" not in doc["$set"]["message_content"]["attachments"][0]

    def test_no_attachments_noop(self):
        doc = {"message_content": {"message_text": "hi"}}
        _strip_file_urls(doc)
        assert doc["message_content"]["message_text"] == "hi"

    def test_no_message_content_noop(self):
        doc = {"room_id": "r1"}
        _strip_file_urls(doc)
        assert doc == {"room_id": "r1"}

    def test_model_dump_excludes_file_url_when_none(self):
        from models.room import UserAttachment

        att = UserAttachment(
            file_id="f1",
            s3_key="k1",
            mime_type="image/png",
            file_name="a.png",
            size_bytes=100,
        )
        dumped = att.model_dump(mode="json")
        assert dumped["file_url"] is None
