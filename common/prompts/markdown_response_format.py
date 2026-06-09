"""Shared markdown formatting rules for Hybro-controlled LLM synthesis outputs."""

HYBRO_MARKDOWN_RESPONSE_FORMAT = """
## Markdown Formatting
- Use `###` section headers for each major section (e.g. `### TL;DR — Top 3`, `### Prioritized items (up to 6)`).
- Put a blank line before every heading and before every list.
- Within each section, use sequential ordered-list numbering starting at 1 (`1.`, `2.`, `3.` …). Restart at 1 for each new section.
- For nested fields under a numbered item, use markdown sub-bullets (`- **Label:** value`). Do not use ASCII space indentation.
- Never write the literal words "4 spaces" in the response — that is a formatting instruction, not content to show the user.

Example shape:
### TL;DR — Top 3
1. First headline
2. Second headline
3. Third headline

### Prioritized items (up to 6)
1. Item title
- **Summary:** One line
- **Paywall:** No

### Recommended next actions
1. First action
2. Second action
""".strip()
