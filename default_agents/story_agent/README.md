# Story Agent

A Hybro default agent that tells and writes engaging stories. Give it a prompt
like "tell me a story about a cat" and it streams back a creative, well-structured
narrative, powered by OpenAI (via LangChain) over the A2A (Agent2Agent) protocol.

This agent ships with Hybro and is built and registered with the backend
automatically when you run `install.sh` — no manual setup required.

## License

This project is licensed under the terms of the [Apache 2.0 License](/LICENSE).

## Contributing

See [CONTRIBUTING.md](/CONTRIBUTING.md) for contribution guidelines.

## Disclaimer

Important: The sample code provided is for demonstration purposes and illustrates the mechanics of the Agent-to-Agent (A2A) protocol. When building production applications, it is critical to treat any agent operating outside of your direct control as a potentially untrusted entity.

All data received from an external agent—including but not limited to its AgentCard, messages, artifacts, and task statuses—should be handled as untrusted input. For example, a malicious agent could provide an AgentCard containing crafted data in its fields (e.g., description, name, skills.description). If this data is used without sanitization to construct prompts for a Large Language Model (LLM), it could expose your application to prompt injection attacks.  Failure to properly validate and sanitize this data before use can introduce security vulnerabilities into your application.

Developers are responsible for implementing appropriate security measures, such as input validation and secure handling of credentials to protect their systems and users.
