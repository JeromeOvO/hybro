from execution.hitl.public_prompt import is_file_upload_request


def test_file_upload_request_requires_strong_verb_and_file_noun():
    assert is_file_upload_request("Please upload the signed PDF in a new message.")
    assert is_file_upload_request("Attach the requested document to your next message.")
    assert not is_file_upload_request("What information is missing from the PDF?")
    assert not is_file_upload_request("Please provide the missing information.")
    assert not is_file_upload_request("Upload your application in a new message.")
    assert not is_file_upload_request("I uploaded the PDF in my last message.")
    assert not is_file_upload_request("Do not upload the PDF; describe it here.")
    assert not is_file_upload_request("I cannot upload the document.")
    assert not is_file_upload_request("Please upload the spreadsheet.")
    assert not is_file_upload_request("Please attach the image.")


def test_file_upload_request_rejects_non_file_alternatives():
    assert not is_file_upload_request("Upload the PDF or paste the relevant text here.")
    assert not is_file_upload_request(
        "Attach the document or provide the details in your reply."
    )
    assert not is_file_upload_request(
        "Paste the relevant text instead, or upload the PDF."
    )


def test_typed_file_prompt_is_authoritative():
    assert is_file_upload_request("Provide the signed form.", prompt_type="file")
    assert not is_file_upload_request("Which region?", prompt_type="text")
