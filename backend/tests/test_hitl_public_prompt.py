from execution.hitl.public_prompt import (
    concrete_agent_input_prompt,
    is_file_upload_request,
    is_internal_agent_contract_prompt,
    public_agent_input_prompt,
)


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


def test_internal_agent_contract_prompt_is_not_public_hitl():
    json_prompt = (
        "Send a JSON object (directly or in a DataPart) containing client and "
        "requested_coverage. Include client.name and requested_coverage.limit."
    )
    dotted_prompt = (
        "Provide client.name, client.employee_count, and claims_history.prior_claims."
    )
    short_json_prompt = "Return JSON containing client.name."
    structured_prompt = (
        "Provide a structured payload containing client and requested_coverage."
    )

    for prompt in (
        json_prompt,
        dotted_prompt,
        short_json_prompt,
        structured_prompt,
    ):
        assert is_internal_agent_contract_prompt(prompt)
        assert concrete_agent_input_prompt(prompt) is None
        assert public_agent_input_prompt(prompt) == (
            "The agent needs additional information."
        )


def test_friendly_agent_question_remains_public_hitl():
    prompt = "What is Acme SaaS Inc.'s annual revenue?"

    assert not is_internal_agent_contract_prompt(prompt)
    assert concrete_agent_input_prompt(prompt) == prompt
