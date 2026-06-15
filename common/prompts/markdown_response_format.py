"""Shared markdown formatting rules for Hybro-controlled LLM synthesis outputs."""

HYBRO_MARKDOWN_RESPONSE_FORMAT = """
## Markdown Formatting
- Use `###` section headers for each major section (e.g. `### TL;DR — Top 3`, `### Prioritized items (up to 6)`).
- Put a blank line before every `###` heading and before every top-level ordered list (`1.` …). Do not put a blank line between a numbered item title and its `-` sub-bullets.
- Put a blank line between numbered items when the next item has its own sub-bullets.
- Within each section, number items sequentially (`1.`, then `2.`, then `3.`). Do not write `1.` for every item.
- Each new `###` section starts numbering over at `1.`.
- For nested fields under a numbered item, use markdown sub-bullets only: `- **Label:** value`.
- Write sub-bullets at column 0 (no ASCII space indentation); Hybro renders them nested under the numbered item above.
- Never write the literal words "4 spaces" in the response — that is a formatting instruction, not content to show the user.

Do not:
- Use `1.` or `1. •` for sub-fields (use `-` only).
- Use Unicode bullets (`•`) as list markers (use `-` only).
- Put multiple numbered items on one line (`1. foo 2. bar`).
- Put a `###` heading on its own line separate from its title.
- Use `1.` or plain prose as a section header when you mean a `###` section.

Example:
### TL;DR — Top 3

1. First headline
2. Second headline
3. Third headline

### Prioritized items (up to 6)

1. Item one title
- **Summary:** One line
- **Paywall:** No

2. Item two title
- **Summary:** One line
- **Paywall:** Yes

### Recommended next actions

1. First action
2. Second action
""".strip()
